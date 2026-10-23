# m6-bvlgari-ksu

荣耀 Magic6 Pro（BVL-AN16，Bvlgari）**厂商开源内核树**上编译 KernelSU 的 ko。

## 为什么

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
构建时把 SUBLEVEL 改成 157 + LOCALVERSION 对齐，压 vermagic；CRC 是否全对要实测。

## 用法

1. 仓库变量 `HONOR_SRC_URL` 填下载地址（或 dispatch 时传 `source_url`）
2. `config/device.config` 放设备真实 config（从 boot.img 用 `scripts/extract-ikconfig` 提取）
3. Actions → `build-ksu-ko` → Run workflow
4. 产物：`kernelsu.ko`

## 判据

- `strings kernelsu.ko | grep sucompat-kp` 有命中 ⇒ kprobe sucompat 编进去了
- `strings kernelsu.ko | grep vermagic` 必须是 `6.1.157-android14-11-g7d3922fade43 ...`
- 装到设备后：`dmesg | grep -i "sucompat"` 出现 `direct syscall-table hooks installed` 或 `sucompat-kp` 注册成功

## 版本策略（2026-10-24 起：uapi 5 / 6 并存）

**核心原则：一份 ko 只配一个管理器。** 管理器启动时会比对 `KERNEL_SU_UAPI_VERSION`，
不相等就显示「需要更新内核」或 ioctl 全部被拒。所以「多管理器支持」= 按管理器出多个 ko 文件，
不是一份 ko 通吃。

| ko 文件 | 源码 / tag | uapi | 配套管理器 | 状态 |
|---|---|---|---|---|
| `kernelsu_ksu331.ko` | `tiann/KernelSU` `v3.3.1` | 5 | 官方 `me.weishu.kernelsu` 32680+ | 维护 |
| `kernelsu_next.ko` | `KernelSU-Next/KernelSU-Next` `v3.4.2` | 5 | Next（伪装包名 `kqvdie.zeykvw.hpbcyd`） | 维护 |
| `kernelsu_custom.ko` | `Baka-SU/BakaSU` `main` | 5 | BakaSU `com.bakasu.bakasu` 35234 | 维护 |
| `kernelsu_sukisu.ko` | `SukiSU-Ultra/SukiSU-Ultra` `v4.2.1-r1` | **6** | SukiSU `com.sukisu.ultra` 41049 | 新增（待真机验收） |
| `kernelsu_finasu.ko` | `LyraVoid/FinaSU` `v1.0.7` | **6** | FinaSU `me.yuki.fina` 32997 | 新增（待真机验收） |
| `kernelsu-os9-selx11.ko` | 旧作者线（BakaSU 4.1.0 代） | 4 | ReSukiSU / BakaSU 旧版 | 兜底，不再升级 |
| `kernelsu_success.ko` | KernelSU 3.2.5 快照（6.1.128） | 2 | — | 仅 A/B 对照 |

已废弃：`tiann v3.2.5/v3.3.0` → uapi 2；`BakaSU v4.2.0-rc3` → uapi 4。

### 四条硬规则

1. **跟 tag，不跟 main。** main 随时改 uapi（BakaSU 就是 `rc3`=4 → `main`=5）。
   要升版本就先把新 tag 的 `uapi/supercall.h` 里的 `KERNEL_SU_UAPI_VERSION` 抄进上表再编。
2. **ko 文件名固定，版本写在 modinfo/vermagic 里。** 升级 = 同名文件替换 + 重算 MANIFEST，
   loader 和 App 的映射表不用动。
3. **fork 用 `ksu_tarball`，别用它的 `setup.sh`。** FinaSU 的 setup.sh 里 `git clone` 写死成
   `tiann/KernelSU`，用它等于拿着别人的源码编自己的 ko。workflow 里填 tarball 地址即走
   「解包 → 手动软链 → Makefile/Kconfig 三件套」。
4. **没在真机跑过的，进 docs 时标「待验收」。**

### APK 侧联动（m6-shell）

- 载荷一变就 **必须 +versionCode**：`PAYLOAD_STAMP = vc<versionCode>-<MANIFEST md5>`，
  vc 不变时设备端戳可能仍然相等 ⇒ 跳过推送 ⇒ 你装的是新 App、跑的是旧载荷。
- 同步载荷：`python m6-jailbreak-core/tools/rebuild_manifest.py --sync`
  （重算 `MANIFEST.md5` + `bundled_ko.md5`，并把 payload 全量拷进 `app/src/main/assets/route/m6/`）
- 产物命名：`dist/m6-shell-<versionName>-{debug,release}.apk`；release 用 `keystore/m6shell-release.jks`。

## 坑

`make ... | tail -60` 会让 tail 提前关管道、make 收到 SIGPIPE 死掉，而管道退出码是 tail 的 0 ⇒ 步骤显示成功但没有任何产物。必须 `set -o pipefail` 或重定向到 build.log。
