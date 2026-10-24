# su 打通步骤（BVL-AN16 实操版）

> 目标：让授权过的 App 里 `su -c id` 输出 `uid=0(root) … context=u:r:ksu:s0`。
> 这份文档只写「怎么做 + 怎么判断成没成」，原理推导见 `status_20261024.md`。

## 一句话结论

**su 可用 = 四条腿同时站住**，缺一条就失败，且失败现象长得几乎一样（都是 `su: inaccessible or not found`
或 `Permission denied`）：

| # | 条件 | 判据 |
|---|---|---|
| 1 | ko 真的装进了内核 | `grep kernelsu /proc/modules` → `… Live 0x… (E)` |
| 2 | `su_path` 进了 insmod 参数 | `ksu_load.log` 里 `KSU_PARAMS=[… su_path=…]` |
| 3 | 这个路径在 App 域**可执行** | 文件存在、`0755`、标签 `u:object_r:apk_data_file:s0` |
| 4 | 该 App 在管理器授权名单里 | 管理器里显示「已授权」 |

---

## 步骤 1：确认这台机能不能装

```bash
adb shell uname -r            # 6.1.157-android14-11-g7d3922fade43
adb shell getprop ro.build.fingerprint
```

ko 的 `vermagic` 必须与 `uname -r` **逐字**相同。不一致就别往下走 —— 先按
`m6-bvlgari-ksu` 的构建流程重编一个。

```bash
grep -a -o 'vermagic=[^ ]*' payload/kernelsu_finasu.ko
```

## 步骤 2：准备 su 落点（**装载之前**）

这是整件事最容易做错的一步。

| 候选落点 | 结论 |
|---|---|
| `/system/bin/su` | 荣耀原厂不存在，`execvp("su")` 直接 ENOENT，靠内核重定向才有 |
| `/dev/su`（KernelSU 默认） | ✗ 需要 `u:object_r:ksu_file:s0`，该类型是 **ko 装载后**才注入 policy 的；装载前 `chcon` 必失败，装载后 loader 已失权 |
| `<app lib>/libksu_su.so` | ✅ **用这个**。自动继承 `apk_data_file`，App 域按类型级规则可 exec，不需要 chcon |

做法：App 把路径写给 loader（App 侧能正确拿到 `nativeLibraryDir`，loader 侧不能）：

```kotlin
// JailbreakShell.kt —— 部署阶段
val lib = context.applicationInfo.nativeLibraryDir
exec("printf '%s' '$lib' > $RD/SELF_LIB")
exec("printf '%s' '${context.packageCodePath}' > $RD/SELF_APK")
```

loader 里：

```sh
SELF_LIB=$(cat "$RD/SELF_LIB" 2>/dev/null)
SU_PATH_RUNTIME="$SELF_LIB/libksu_su.so"
cp -f "$RD/ksud" "$SU_PATH_RUNTIME"   # ksud 就是 su 前端，按 argv[0] 走 su 流程
chmod 755 "$SU_PATH_RUNTIME"
```

⚠ **不要在 loader 里跑 `pm path`** —— loader 运行在 exploit 伪造的 `u:r:kernel:s0`，
`pm` / `cmd package` / `dumpsys` 全部返回空（`app_process` 被 exec 拒绝）。

## 步骤 3：其它收尾也全部前置

ko 装载成功后，**装载它的那个进程会被踢出 root**：

```
can't open /proc/modules: Permission denied
can't create /data/local/tmp/xxx.tmp: Permission denied   ← uid 0 必能写这里
getenforce / id / sleep: inaccessible or not found
```

原因：伪造的 SID 被新注入的 policy 作废，`su_cred` 的 task_work 又回滚了 cred。
`fork` 一个 watcher 也没用 —— 它继承同一份 cred，一起失效。

所以这些必须在 `insmod` **之前**做完：

- [x] `su_path` 指向的文件（步骤 2）
- [x] `/data/adb/ksud` 铺好（`chmod 755`、属主 root）
- [x] 需要的 ko 参数（`su_cred=2`、`su_check=1`、`su_path=…`、`mgr_pkgs=…`、`allow_shell`）
- [x] 需要的话把 App 加进 allowlist（`grant_apps=Y` 或管理器手动授权）

## 步骤 4：装载并判定 LIVE

```sh
insmod_cmd ... "$RD/.run_kernelsu.ko" su_cred=2 su_check=1 su_path="$SU_PATH_RUNTIME"
LOAD_RC=$?
```

装载后**不要**再依赖 `cat /proc/modules`（自己已经没权限），判据改成：

```sh
# loader 里
if [ "$MOD" = "0" ] && [ "$LOAD_RC" = "0" ]; then
    echo "kernelsu LIVE（按 load_ko_exit=0 判定）"
fi
```

外部验证（adb shell 就能看，`/proc/modules` 对 shell 可读）：

```bash
adb shell grep kernelsu /proc/modules
# kernelsu 339968 1 - Live 0xffffffdaf2744000 (E)
```

## 步骤 5：授权 App

在管理器（FinaSU `me.yuki.fina` / SukiSU `com.sukisu.ultra`）里把目标 App 打开授权。
授权名单的 uid 就是 sucompat 判定用的那个 uid —— **没授权就不会重定向**。

## 步骤 6：验证（**必须用 App 域**）

```sh
# 在 aShell（或任何已授权的 App）里
su -c id
# uid=0(root) gid=0(root) groups=0(root) context=u:r:ksu:s0
```

### 不要用 adb shell 测

`adb shell` 是 `uid=2000(shell)`，不在 allowlist ⇒ sucompat 按设计不重定向 ⇒
永远 `su: inaccessible or not found`，**和「内核钩子坏了」表现完全一样**。
`allow_shell=1` 只是 insmod 参数，不等于把 2000 写进持久 allowlist。

这一点在本项目上真实误导过一整轮排查，写了好几版 patch 才发现是测量方法的问题。

---

## 排错对照表

| 症状 | 原因 | 处理 |
|---|---|---|
| `/proc/modules` 没有 `kernelsu` | 装载失败 | 看 `loadko_result.log`：`load_ko_exit=3` = 符号没解析完；`127` = 缺二进制；`126` = 权限标签 |
| 有 `kernelsu` 但参数里没 `su_path` | 步骤 2 没做成 | 检查 `KSU_PARAMS` 那行；补 `SELF_LIB` 并重装 App |
| `su` → `inaccessible or not found` | ① 用的 adb shell（uid 2000）② 落点不可执行 ③ 没重定向 | 先换 aShell 测；再看 `ls -Z` 落点标签 |
| `su` → `Permission denied` | 落点标签不对（如 `device:s0`）或 App 未授权 | 换到 `apk_data_file`；管理器里重新授权 |
| 授了权还是不行 | 授权写进了旧 boot 的名单 / `grant_apps=N` | 重启后重授权；或 insmod 时带 `grant_apps=Y` |
| 装载后 loader 报一堆 `Permission denied` | 正常（已失权） | 不用修，把收尾前置即可 |
| 装载后 `Segmentation fault` / `rc=139` | 内核钩子崩（信号不是权限） | 换 ko 版本 / 换管理器做 A/B |

## 最小命令清单（复盘用）

```bash
# 1 环境对齐
uname -r; grep -a -o 'vermagic=[^ ]*' kernelsu_*.ko

# 2 落点（App 侧写好 SELF_LIB，loader 侧拷 ksud）
cat $RD/SELF_LIB; ls -lZ "$(cat $RD/SELF_LIB)/libksu_su.so"

# 3 装载并看参数
grep KSU_PARAMS $RD/ksu_load.log | tail -1

# 4 模块状态（adb 可读）
grep kernelsu /proc/modules

# 5 授权后，在 App 里测
su -c id
```
