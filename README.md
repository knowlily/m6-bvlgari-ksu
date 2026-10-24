# m6-bvlgari-ksu

荣耀 Magic6 Pro（`BVL-AN16`，Bvlgari）**厂商开源内核树**上编 KernelSU 系 ko，并用 GitHub Actions 出包。

> **当前状态（2026-10-24 收工）**
> **全链路已通**：模块 Live（`kernelsu … Live`、`modules=1 root=1 physrw=1`、SELinux Enforcing），
> 管理器授权 App 正常，**授权过的 App 里 `su -c id` = `uid=0(root) … context=u:r:ksu:s0`**。
> 唯一限制：`adb shell`（uid 2000）不在 allowlist ⇒ 那里敲 `su` 仍 ENOENT，这是授权模型，不是 bug。
> 快照见 [`docs/status_20261024.md`](docs/status_20261024.md)。

## 为什么用厂商树

- 现成的 `kernelsu-os9-selx11.ko`（6.1.157）只有 syscall 表 hook，**没有 kprobe 版 sucompat** ⇒ App 授权 root 拿不到。
- kit/CGA 那份带 kprobe sucompat 的 ko 是 6.1.128，vermagic 不匹配。
- 用 ACK 自编 6.1.157 会 panic：`struct module` 1024 B ≠ 厂商 1088 B。
- 用**厂商树**编，ABI 天然一致，且能编出带 kprobe sucompat 的版本。

## 源码

荣耀开源中心 → 手机 → 第 3 页：

```
MagicOS10.0-ROM-Bvlgari_MagicOS10.0_Code_Opensource
  → 荣耀Magic6 / Magic6 Pro / 至臻版 / RSR保时捷设计
```

下载走浏览器会话（curl 需带 UA + Referer）：537 MB zip，内含 `Code_Opensource.tar.gz` → `Code_Opensource/kernel`

⚠ 已确认：包里 `kernel/Makefile` 是 **6.1.128**，而设备跑 **6.1.157**。
构建时用 `sublevel=157` + `localversion=-android14-11-g7d3922fade43` 压对齐，CRC 需实测（见下）。

## 构建

`.github/workflows/build-ksu-ko.yml`，两种输入方式：

| 参数 | 说明 |
|---|---|
| `ksu_repo` + `ksu_ref` | 上游仓库与 tag（只跟 tag，不跟 main） |
| `ksu_tarball` | fork 专用：直接给 tarball，绕开它的 `setup.sh` |
| `ko_alias` | 产物后缀，出 `kernelsu_<alias>.ko` |
| `ksu_debug` | `true` 带 `CONFIG_KSU_DEBUG`（真机排查用） |
| `sublevel` / `localversion` | 对齐 vermagic，本机固定 `157` / `-android14-11-g7d3922fade43` |

```bash
gh workflow run build-ksu-ko.yml -f ksu_repo=LyraVoid/FinaSU -f ksu_ref=v1.0.9 \
  -f ksu_tarball=https://github.com/LyraVoid/FinaSU/archive/refs/tags/v1.0.9.tar.gz \
  -f ko_alias=finasu -f ksu_debug=true -f sublevel=157 \
  -f localversion=-android14-11-g7d3922fade43
```

已跑过：`39984701370`（SukiSU v4.2.1-r1）· `39984717046`（FinaSU v1.0.7）·
`39990510689`（FinaSU v1.0.8）· `40056659332`（FinaSU v1.0.9）。

## ko 清单

| ko 文件 | 大小 | 源码 / tag | uapi | 配套管理器 | 状态 |
|---|---|---|---|---|---|
| `kernelsu_ksu331.ko` | 6.1 MB | `tiann/KernelSU` `v3.3.1` | 5 | `me.weishu.kernelsu` | 维护 |
| `kernelsu_next.ko` | 6.4 MB | `KernelSU-Next` `v3.4.2` | 5 | `kqvdie.zeykvw.hpbcyd`（伪装名） | 维护 |
| `kernelsu_custom.ko` | 10.3 MB | `Baka-SU/BakaSU` `main` | 5 | `com.bakasu.bakasu` | 维护（历史真机最稳） |
| `kernelsu_sukisu.ko` | 7,465,008 B | `SukiSU-Ultra` `v4.2.1-r1` | **6** | `com.sukisu.ultra` | 待真机验收 |
| `kernelsu_finasu.ko` | 7,489,752 B | `LyraVoid/FinaSU` `v1.0.9` | **6** | `me.yuki.fina` | ✅ 真机通过（Live + su 可用） |
| `kernelsu-os9-selx11.ko` | 5.5 MB | 旧作者线（BakaSU 4.1.0 代） | 4 | ReSukiSU / BakaSU 旧版 | 兜底，不再升级 |
| `kernelsu_success.ko` | 5.5 MB | KernelSU 3.2.5 快照（6.1.128） | 2 | — | 仅 A/B 对照 |

已废弃：`tiann v3.2.5/v3.3.0`（uapi 2）、`BakaSU v4.2.0-rc3`（uapi 4）。

## 判据

**构建侧**

```bash
grep -a -o 'vermagic=[^ ]*' kernelsu_<alias>.ko    # 必须 6.1.157-android14-11-g7d3922fade43
strings kernelsu_<alias>.ko | grep sucompat        # kprobe 版 sucompat 编进去了
```

CRC 用 `config/device_crc.json`（3,998 条，从本机内核采）逐条比对 `__versions` 段，必须全一致。
FinaSU v1.0.9 实测 **179/179 一致**，239 个 UND 符号被 271 条清单全覆盖。

**真机侧**

```bash
grep kernelsu /proc/modules        # kernelsu … Live（E）
dmesg | grep -iE "kernelsu|sucompat"
su -c id                           # 期望 uid=0(root) … context=u:r:ksu:s0
```

## 版本策略（uapi 5 / 6 并存）

**核心原则：一份 ko 只配一个管理器。** 管理器启动时比对 `KERNEL_SU_UAPI_VERSION`，
不相等就显示「需要更新内核」或 ioctl 全被拒。「多管理器支持」= 按管理器出多个 ko，不是一份通吃。

### 四条硬规则

1. **跟 tag，不跟 main。** main 随时改 uapi（BakaSU 就是 `rc3`=4 → `main`=5）。
   升版本先把新 tag 的 `uapi/supercall.h` 里 `KERNEL_SU_UAPI_VERSION` 抄进上表再编。
2. **ko 文件名固定，版本写进 modinfo/vermagic。** 升级 = 同名替换 + 重算 MANIFEST，
   loader 与 App 的映射表不用动。
3. **fork 用 `ksu_tarball`，别用它的 `setup.sh`。** FinaSU 的 `setup.sh` 把 `git clone`
   写死成 `tiann/KernelSU`，照跑等于拿别人的源码编自己的 ko。
4. **没在真机跑过的，进 docs 时标「待验收」。**

## 实测结论（2026-10-23 ~ 10-24，避免重复踩）

### 1. 装载成功的三个必要条件

`ko_patch2` 是「全有或全无」：符号清单少一条就整体 ABORT，且**不写文件**。

1. `next_sym_addr_uapi5.txt` 覆盖 ko 的**全部** UND 符号（当前 271 条）；
2. ko vermagic 与 `uname -r` **逐字**相同（`-dirty`、`-4k` 都算差异）；
3. 每轮先 `cp` 到 `$RD/.run_<name>.ko` 再 patch —— 就地改原件会让下次开机 slide 叠加。

特例：`commit_creds` / `prepare_kernel_cred` 在本机 `/proc/kallsyms` 里**只有 `__ksymtab_*`**，
裸符号要靠静态地址补进清单。

### 2. 装载后 loader 必定失权 —— 收尾必须前置

`load_ko` 成功后，装载它的进程会被踢出 root：

```
can't open /proc/modules: Permission denied
can't create /data/local/tmp/xxx.tmp: Permission denied      ← uid 0 必能写这里
getenforce / id / sleep: inaccessible or not found
```

原因：loader 以 exploit 伪造的 `u:r:kernel:s0` 运行，ko 一装、policy 一注入，伪造 SID 失效；
`su_cred` 的 task_work 也会回滚 cred。

⇒ **su 落点、`/data/adb/ksud`、所有 insmod 参数都在装载前准备好。**
`fork` 一个「装载前 watcher」不管用 —— 它继承同一份 cred，会一起失效。

### 3. su 落点用 `apk_data_file`，别指望 `/dev/su`

| 落点 | 标签 | 结论 |
|---|---|---|
| `/dev/su` | 需 `u:object_r:ksu_file:s0` | ✗ 该类型是 ko 注入的，装载前 `chcon` 必失败；装载后没权限打 |
| `<app lib>/libksu_su.so` | 自动 `apk_data_file` | ✓ App 域可 exec，无需 chcon |
| `/system/bin/su` 真文件 | — | 荣耀原厂不存在，靠 sucompat 重定向 |

App 用 `applicationInfo.nativeLibraryDir` 把路径写给 loader（`$RD/SELF_LIB`）。
**不要在 loader 里跑 `pm path`** —— `u:r:kernel:s0` 下 `pm` / `cmd package` / `dumpsys` 全返回空。

### 4. `rc=139` 是信号，不是权限

`139 = 128 + 11 = SIGSEGV`。权限问题只会给 EACCES/EPERM，**绝不会给信号**。
装载后 exec 段错误 ⇒ 内核替进程执行 execve 时 oops（钩子按编译期偏移读写
`linux_binprm` / `cred` / `vfsmount`，与厂商 6.1.157 结构对不上）。
佐证：FinaSU v1.0.8 只是 access/stat 被拒，v1.0.9 新增 `mount_view_filter`（hook `seq_show`/`open`）
后升级成 SIGSEGV。

## 已知问题

1. **`adb shell`（uid 2000）里 `su` 不生效** —— 不是 bug，是授权模型：shell 不进 allowlist 就不重定向
   （`allow_shell=1` 只是 insmod 参数，不等于把 2000 写进持久 allowlist）。**测 su 必须用 App 域**
   （aShell 之类，先在管理器里授权），用 adb shell 测会得到假阴性——这条踩过，2026-10-24 一度误判成
   "sucompat 不兼容"。
2. **`resize_pipe_slots soft-fail`** —— 管道页配额不足，降 exploit 命中率；重启最有效。
3. **`ksud unload` 必 panic** —— 不能靠卸载换 ko，只能重启；一轮 boot 只有一发。

## APK 侧联动（m6-shell）

- 载荷一变就 **必须 +versionCode**：`PAYLOAD_STAMP = vc<versionCode>-<MANIFEST md5>`，
  vc 不变时设备端戳可能仍相等 ⇒ 跳过推送 ⇒ 装了新 App、跑的是旧载荷。
- 同步载荷：`python m6-jailbreak-core/tools/rebuild_manifest.py --sync`
- 产物：`m6-shell/dist/m6-shell-<versionName>-{debug,release}.apk`；release 用 `keystore/m6shell-release.jks`。

## 文档索引

| 文件 | 内容 |
|---|---|
| `docs/status_20261024.md` | **现状快照（先看这个）**：版本/ko/已验证项/未解决项/下一步 |
| `docs/progress_20261024.md` | 当天全过程：uapi6 适配、FinaSU v1.0.8→v1.0.9、动态管理器、符号清单、su 落点两修、错误普查 |
| `docs/progress_20261022.md` / `20261021.md` | 早期链路：双 kprobe sucompat、MTE 去标签、载荷与 selfcheck 体例 |
| `docs/new-device-checklist.md` | 另一台同型号同固件机器的直用清单 |

## 坑

`make ... | tail -60` 会让 tail 提前关管道、make 收到 SIGPIPE 死掉，而管道退出码是 tail 的 0
⇒ 步骤显示成功但没有任何产物。必须 `set -o pipefail` 或重定向到 build.log。
