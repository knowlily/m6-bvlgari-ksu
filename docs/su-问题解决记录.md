# su 问题解决记录（复盘）

> 记录「明明模块装上了，su 却用不了」这个问题是怎么一步步定位、修好、以及中途误判在哪。
> 操作手册见 [`su-playbook.md`](su-playbook.md)，当前状态见 [`status_20261024.md`](status_20261024.md)。

## 一、机型与环境

| 项 | 值 |
|---|---|
| 机型 | 荣耀 Magic6 Pro（`BVL-AN16`，device `HNBVL`） |
| 系统 | MagicOS 10.0.0.170 `C00E170R202P8`，Android 16 |
| 内核 | `6.1.157-android14-11-g7d3922fade43` |
| fingerprint | `HONOR/BVL-AN16/HNBVL:16/HONORBVL-AN16/10DLDLD170C00E170:user/release-keys` |
| SELinux | Enforcing（严格模式，全程未关） |
| 内核模块 | FinaSU v1.0.9（uapi6）/ SukiSU v4.2.1-r1（uapi6），均厂商树自编 |
| 管理器 | `me.yuki.fina`（v1.0.9）/ `com.sukisu.ultra`（v4.2.1-r1） |

**这套东西绑定的不是机型，是内核镜像**：`uname -r` 与 ko 的 vermagic 逐字相同即可，
同型号同固件的机器可直接用；换了内核版本要重编。

## 二、问题现象

模块明明装上了，`su` 却用不了，而且**同一个问题在不同地方的表现不一样**：

```
① su -c id              → /system/bin/sh: su: inaccessible or not found
② loader 装载后自检      → can't open /proc/modules: Permission denied
                          sleep / id / getenforce: inaccessible or not found
③ loader 收尾 exec       → Segmentation fault（rc=139）
```

当时最大的困惑：App 自己的 root 是好的（`M6_VERDICT: ROOT`、管理器授权也正常），
只有 `su` 这条命令不通。这说明问题不在「内核没装上」，而在别处。

## 三、排查过程（四个阶段，前三个都是错的）

### 阶段 1：怀疑 ko 没真正装载 → 证伪

证据（`loadko_result.log`）：

```
[ko_patch] slide=0x244b400000
sym file: 271 entries
OK: 239 symbols -> SHN_ABS
read .run_kernelsu_finasu.ko (7489752 bytes)
undefined symbols to resolve: 0
[+] module loaded OK!
/proc/modules → kernelsu 339968 1 - Live 0xffffffdaf2744000 (E)
```

**结论：装载完全成功。** 这条路堵死。

### 阶段 2：怀疑 su 落点不可用 → 真问题之一

日志：

```
[su] 取不到本包路径，跳过
[su] /dev/su 标签异常（u:object_r:device:s0）⇒ 退回安装目录
[su] /dev/su 不可用且没有安装目录 ⇒ 本轮 su 目标放弃
KSU_PARAMS=[… su_cred=2]        ← 没有 su_path
```

根因两条：

1. `_su_setup_apk()` 写死查旧包名 `com.dsh.ppgroot`，取不到就整体 `return 0`；
2. **`/dev/su` 这条路本来就不可行** —— 它需要 `u:object_r:ksu_file:s0`，而该类型是
   **ko 装载后才注入 policy 的**，装载前 `chcon` 必失败（实测停在 `device:s0`），
   装载后 loader 又已经没权限打标签。

修复：su 落点改到 **`<app lib>/libksu_su.so`**，自动继承 `apk_data_file`，
App 域按类型级规则可 exec，不需要 chcon、不依赖模块先装。

### 阶段 3：怀疑路径取不到 → 真问题之二

修复后发现 `su_path` 还是空的。根因：loader 运行在 exploit 伪造的 `u:r:kernel:s0` 域，
在那里 `pm path` / `cmd package` / `dumpsys` **全部返回空**（`app_process` 被 exec 拒绝）。

修复：让 **App 侧**写两个 marker 给 loader 读：

```kotlin
exec("printf '%s' '$lib' > $RD/SELF_LIB")     // applicationInfo.nativeLibraryDir
exec("printf '%s' '$pkgPath' > $RD/SELF_APK") // packageCodePath
```

### 阶段 4：装载后 loader 失权 → 收拾残局

这套日志一度被当成「模块坏了」：

```
can't open /proc/modules: Permission denied
can't create /data/local/tmp/sh3p4bje.tmp: Permission denied   ← 铁证
health_check=0 ⇒ module missing - skipping ksud stages
```

`/data/local/tmp` 是 `0771`，**uid 0 必然能写** —— 出现 `Permission denied` 就说明
装载后这个进程已经**不是 root** 了。

机制：loader 的 root 来自 exploit 在 cred 里塞的伪造 SID；ko 一装、policy 一注入，
这个 SID 失效；同时 `su_cred=2` 的 task_work 在任务切换时回滚 cred。

修复思路：**把收尾全部前置。**

- `/data/adb/ksud` 从装载后挪到 `insmod` 之前铺
- LIVE 判定加兜底：`MOD=0 && LOAD_RC=0` 即认为模块已装（不再依赖读 `/proc/modules`）
- 试过 fork 一个「装载前 root watcher」——**无效**，它继承同一份 cred，一起失效

## 四、最大的坑：误判"sucompat 与内核不兼容"

到这一步模块 Live、su_path 正确、落点可执行、App 也授权了，但 adb 里
`su -c id` 依然 `inaccessible or not found`。加上同一时期 loader 收尾出现
`Segmentation fault`（`rc=139`），于是得出一个听起来很合理的结论：

> 「FinaSU v1.0.9 的 sucompat 钩子按编译期结构偏移访问 `linux_binprm`，与荣耀 6.1.157 对不上，
> 内核 oops ⇒ SIGSEGV。建议换 SukiSU 或回退 uapi5。」

**这个结论是错的。** 真正的错误在测量方法：

```
adb shell 的身份 = uid=2000(shell)
```

uid 2000 **不在 allowlist** 里，sucompat 按设计不会给它重定向 ⇒ 必然 ENOENT。
这和「钩子坏了」的表现一模一样，所以整轮排查都建立在一个假阴性上。
（`allow_shell=1` 只是 insmod 参数，不等于把 2000 写进持久授权名单。）

## 五、真正解决的一步

在**授权过的 App（aShell）**里测：

```sh
su -c id
# uid=0(root) gid=0(root) groups=0(root) context=u:r:ksu:s0
```

**sucompat 一直是好的。**

于是问题收敛成一张表 —— su 可用 = 四个条件同时成立：

| # | 条件 | 当时状态 |
|---|---|---|
| 1 | ko 装进内核 | ✅ 一直没问题 |
| 2 | `su_path` 进 insmod 参数 | ❌ 阶段 2/3 修好 |
| 3 | 落点在 App 域可执行 | ❌ 改成 `apk_data_file` |
| 4 | 该 App 被授权 | ✅ 管理器正常 |

## 六、最终方案

```
kernelu ko 装载 → su_cred=2 + su_check=1 + su_path=<app lib>/libksu_su.so
       ↓
App 执行 execve("/system/bin/su")
       ↓
ko 的 kprobe do_execveat_common 命中 → 文件名换成 su_path
       ↓
begin_new_exec / security_bprm_committing_creds → cred 换成 uid 0 + u:r:ksu:s0
       ↓
管理器按 allowlist 放行 → uid=0(root) context=u:r:ksu:s0
```

FinaSU v1.0.9 与 SukiSU v4.2.1-r1 两条 uapi6 线均实测通过；
uapi5 的 BakaSU / KernelSU v3.3.1 / KernelSU-Next v3.4.2 历史上也验证过。

## 七、教训（三条）

1. **测 su 必须用 App 域。** adb shell / Shizuku 都是 uid 2000，会被授权模型挡掉，
   表现和内核钩子坏掉完全一样。这一条代价最大。
2. **区分「权限」和「信号」。** 权限问题只会给 EACCES/EPERM，`rc=139` 是 SIGSEGV —
   现象不同、层也不同，不要混为一谈。
3. **装载后进程会掉权是设计，不是故障。** 凡是装载后要做的事，一律前置；
   fork 子进程也不救，它共享同一份 cred。

## 八、相关文件

| 文件 | 内容 |
|---|---|
| `docs/su-playbook.md` | 操作手册：四条件 / 六步 / 排错对照表 |
| `docs/status_20261024.md` | 现状快照 |
| `docs/progress_20261024.md` | 当天逐条日志 + 结论修正 |
| `payload/ksu_loader.sh` | su 落点、SELF_LIB、装载判定、参数拼装都在这里 |
| `m6-shell/JailbreakShell.kt` | 写 `SELF_LIB` / `SELF_APK` 的 App 侧代码 |
