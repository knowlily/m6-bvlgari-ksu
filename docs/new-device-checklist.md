# 新机直用清单（另一台 BVL-AN16，固件 10.0.0.170）

适用范围：荣耀 Magic6 Pro `BVL-AN16`，内核 `6.1.157-android14-11-g7d3922fade43`
（MagicOS 10.0.0.170）。固件不同 ⇒ vermagic 不同 ⇒ 本清单不适用，需要重编 ko。

## 一、需要人做两件事

```
1. 装管理器（用户自选：官方 KernelSU / KernelSU-Next / BakaSU）
2. 打开「一键越狱」→ 点一次提权
```

不需要：数据线、USB 调试、电脑、marker 文件、任何命令行。

## 二、装哪个管理器 → 自动配哪份 ko

| 管理器包名 | 用哪份 ko |
|---|---|
| `me.weishu.kernelsu`（官方内核版 Manager） | `kernelsu_ksu331.ko`（tiann v3.3.1） |
| `kqvdie.zeykvw.hpbcyd`（Next 伪装包名） | `kernelsu_next.ko`（Next v3.4.2） |
| `com.bakasu.bakasu` / `com.resukisu.*` / 没装 | `kernelsu_custom.ko`（BakaSU main） |

识别顺序：`MANAGER_PKG` marker → App 私有文件 `files/manager_pkg` → **`pm list packages` 自动识别**（2026-10-23 新增）。
都没有时退回 `kernelsu_custom.ko`，并在日志里写明。

## 三、越狱完看日志里的 selfcheck 块

```
=== selfcheck ===
  内核      : 6.1.157-android14-11-g7d3922fade43
  ko        : kernelsu_ksu331.ko
  管理器    : me.weishu.kernelsu (uid=11207)
  su_cred   : 2
  su_dbg    : 0
  su_check  : 1
  SELinux   : Enforcing
  ksud      : 4990848 字节
=== selfcheck end ===
```

逐行判读：

| 行 | 期望 | 不对时 |
|---|---|---|
| 内核 | `6.1.157-android14-11-g7d3922fade43` | 固件版本不对，ko 的 vermagic 不匹配，模块装不上 |
| ko | 与所装管理器匹配 | 装错管理器 → ioctl 全被拒 → 管理器显示"未安装" |
| 管理器 | 有包名 + uid | 没装管理器：su 授权没入口，先装再越狱 |
| su_cred | `2` | 是 1 ⇒ 只换 uid 不换域，写不了 sysfs/系统目录 |
| su_dbg | `0` | 是 1 ⇒ 在刷调试日志，删掉 `SU_CHECK_DBG` marker 后重新越狱 |
| SELinux | `Enforcing` | Permissive 说明收尾没跑完 |
| ksud | 非 0 | 部署失败，看上面的 `[ksud]` 行 |

## 四、验收：一条命令

任意终端 App（aShell / Termux / MT 管理器终端）**本地模式**：

```sh
su -c id
```

成功的样子：

```
uid=0(root) gid=0(root) groups=0(root) context=u:r:ksu:s0
```

`context=` 不是 `u:r:ksu:s0` 就算失败：
- `u:r:untrusted_app:s0`（uid 是 0 但域没换）⇒ `su_cred` 不是 2
- 仍是 `10244(u0_a244)` ⇒ 连重定向都没走到，看 `su-check` / `SU-EXEC` 日志

## 五、运行时开关（改行为不用重编、不用重启）

写 `/sys/module/kernelsu/parameters/` 下的参数，只有 root 域能写：

| 参数 | 值 |
|---|---|
| `su_cred` | 0 只重定向 / 1 手工改 cred（uid 0，域不变）/ 2 换域（默认） |
| `su_check` | 1 开 access/stat 伪装（默认） |
| `su_dbg` + `su_dbg_uid` | 调试：对该 uid 的每次 access/stat 打日志（含 ptr） |

marker（`/data/local/tmp/route/m6/`，越狱时读）：

| marker | 作用 |
|---|---|
| `NO_SUCRED` | `su_cred=0`，只改文件名 |
| `SU_CRED_PLAIN` | `su_cred=1`，旧行为 |
| （都不建） | `su_cred=2`，换域 —— **现默认** |
| `SU_CHECK_DBG=<uid>` | 打开调试并盯这个 uid |
| `ALLOW_FOREIGN_KO` | 允许用用户自己丢进来的 ko（不校验 md5） |

## 六、已知限制

0. **su 落点是 `/dev/su`**（tmpfs + `u:object_r:ksu_file:s0`）。软重启不清、装新 APK 也不影响；
   完整重启后 `/dev` 是新的，需要重新越狱一次（loader 会重建）。不要把它改成 `/data/app`
   或 `/data/local/tmp` 下的路径 —— 前者会被 PackageManager 清，后者 App 域不能执行。
1. 提权成功后按钮变灰，**不能二次提权**；换 ko 必须重启重来越狱。
2. `ksud unload` 必 panic，不能靠卸载改参数。
3. 重启后模块消失，需要重新点一次提权。
4. `su` 的落点是 App 自己的 lib 目录（`/data/app/.../lib/arm64/libksu_su.so`），反 root 检测能看到这个痕迹。

## 七、出问题的排查起点

```
/data/local/tmp/route/m6/ksu_load.log    ← loader 全量日志（含 selfcheck）
/data/local/tmp/route/m6/kmsg_cap.txt    ← 内核日志快照
/data/local/tmp/route/m6/loadko_result.log
```

内核侧关键字：`su-check` / `SU-EXEC` / `su-redirect` / `su-twerk escape` / `unsupported RELA` / `Kernel panic`。
