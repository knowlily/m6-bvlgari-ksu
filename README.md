# m6-bvlgari-ksu

**2026-10-27：OS10 阶段结案** —— MagicOS 10（170-175 内核）已打通、可用，成功率 ≈56%，
瓶颈在 exploit 二进制内部，研究到此截止。结案报告见
[`docs/status_20261027_os10-final.md`](docs/status_20261027_os10-final.md)；
完整归档见 `m6-jailbreak-core/docs/09-os10-archive.md`。下一站：https://github.com/knowlily/m6-os11-ksu

## ko 清单

| ko | 源码 / tag | uapi | 管理器 | 状态 |
|---|---|---|---|---|
| `kernelsu_custom.ko` | BakaSU `main` | 5 | `com.bakasu.bakasu` | ✅ 历史验证（最稳） |
| `kernelsu_ksu331.ko` | KernelSU `v3.3.1` | 5 | `me.weishu.kernelsu` | ✅ 验证 |
| `kernelsu_next.ko` | KernelSU-Next `v3.4.2` | 5 | `kqvdie.zeykvw.hpbcyd` | ✅ 验证 |
| `kernelsu_finasu110.ko` | FinaSU `v1.0.10` | 6 | `me.yuki.fina` | 待真机（默认） |
| `kernelsu_finasu.ko` | FinaSU `v1.0.8` | 6 | `me.yuki.fina` | ✅ 验证（无 mount_view_filter，最保守） |
| `kernelsu_sukisu.ko` | SukiSU `v4.2.1-r1` | 6 | `com.sukisu.ultra` | ✅ 验证 |
| `kernelsu_suki421.ko` | SukiSU `v4.2.1-r2` | 6 | `com.sukisu.ultra` | 待真机（2.31 新加） |
| `kernelsu_basu420.ko` | BakaSU `main@d5e9e025`（= APK 35252） | 6 | `com.resukisu.resukisu` | 待真机（2.31 新加） |
| `kernelsu-os9-selx11.ko` | 旧作者线 | 4 | ReSukiSU / BakaSU 旧版 | 兜底 |
| `kernelsu_success.ko` | KernelSU 3.2.5（6.1.128） | 2 | — | A/B 对照 |

> **怎么选 ko**：不要按包名，按管理器里 ksud 的**版本串**。SukiSU 的发行版会随机改包名（实测 `rqglxo.qzizju.uholjr`）也会把 ksud 改名成 `libeexgslcpwg.so`，包名白名单追不上。
> loader 现在做的是：抽 ksud → 读版本串 → `4.2.0-rc3-81-gd5e9e025`(BakaSU main) 配 `kernelsu_basu420.ko`；`4.2.1-r2`(SukiSU) 配 `kernelsu_suki421.ko`；`1.0.x`(FinaSU) 配 `kernelsu_finasu110.ko`。
>
> ⚠ **BakaSU 的 uapi 分两条线**：tag `v4.2.0-rc3` 是 **uapi4**，而 tag 之后 81 个提交的 `main`（`d5e9e025`，也就是 35252 那个 APK）是 **uapi6**。管理器界面显示 6 就对，用 main 编的 ko。

## 为什么用厂商树

ACK 自编会 panic（`struct module` 1024 B ≠ 厂商 1088 B）；现成 6.1.157 的 ko 没有 kprobe 版 sucompat；
kit 那份 6.1.128 vermagic 不匹配。用厂商树编，ABI 天然一致。
源码：荣耀开源中心 → 手机 → 第 3 页 → `Bvlgari_MagicOS10.0_Code_Opensource`（537 MB）。
包里 `Makefile` 是 **6.1.128**，设备跑 **6.1.157** ⇒ 用 `sublevel=157` + `localversion` 对齐。

## 构建

```bash
gh workflow run build-ksu-ko.yml -f ksu_repo=LyraVoid/FinaSU -f ksu_ref=v1.0.9 \
  -f ksu_tarball=https://github.com/LyraVoid/FinaSU/archive/refs/tags/v1.0.9.tar.gz \
  -f ko_alias=finasu -f ksu_debug=true -f sublevel=157 \
  -f localversion=-android14-11-g7d3922fade43
```

| 参数 | 说明 |
|---|---|
| `ksu_repo` + `ksu_ref` | 上游仓库与 tag |
| `ksu_tarball` | fork 专用，绕开它的 `setup.sh` |
| `ko_alias` | 产物名 `kernelsu_<alias>.ko` |
| `ksu_debug` | 带 `CONFIG_KSU_DEBUG` |
| `sublevel` / `localversion` | 对齐 vermagic（本机 `157` / `-android14-11-g7d3922fade43`） |

## 判据

- `vermagic` 与 `uname -r` **逐字**相同（`-dirty` 也算差异）
- `__versions` CRC 与 `config/device_crc.json` 全一致（FinaSU v1.0.9 实测 179/179）
- `strings kernelsu_*.ko | grep sucompat` 有输出
- 真机：`grep kernelsu /proc/modules` 见 `Live`；授权 App 里 `su -c id` 得 `uid=0`

## 版本策略

**一份 ko 只配一个管理器**（管理器比对 `KERNEL_SU_UAPI_VERSION`，不等就 ioctl 全拒）。

1. 跟 tag 不跟 `main`（main 随时改 uapi）
2. ko 文件名固定，升级 = 同名替换 + 重算 MANIFEST
3. fork 用 `ksu_tarball`，别跑它的 `setup.sh`（FinaSU 的写死 `tiann/KernelSU`）
4. 没在真机跑过的标「待验收」

## 必踩的坑

- **装载后 loader 必失权**（伪造 SID 被新 policy 作废 + `su_cred` task_work 回滚）
  ⇒ su 落点、`/data/adb/ksud`、insmod 参数全在装载前备好；fork watcher 没用，它一起失效
- **su 落点用 `apk_data_file`**（`<app lib>/libksu_su.so`），别用 `/dev/su`（`ksu_file` 装载前不存在）；
  loader 里别跑 `pm path`（`u:r:kernel:s0` 下返回空），要 App 写 `$RD/SELF_LIB`
- **测 su 必须在 App 域**：`adb shell` 是 uid 2000，不在 allowlist ⇒ 永远 ENOENT，和钩子坏了长得一样
- **`ko_patch2` 全有或全无**：符号少一条就 ABORT 且不写文件；先 `cp` 到 `.run_<name>.ko` 再 patch
- 载荷一变必须 +`versionCode`（`PAYLOAD_STAMP` 不变就跳过推送）
- `ksud unload` 必 panic，一轮 boot 只有一发

## 文档索引

| 文件 | 内容 |
|---|---|
| `docs/stability-diff-20261024.md` | 2.8 不稳 vs 老壳 3.16.34 差异清单 + 2.10 优化 |
| `docs/su-完整技术报告.md` | **完整技术报告**（给外部读者）：原理/步骤/失败模式/实测数据/换机型清单 |
| `docs/su-问题解决记录.md` | **复盘**：机型、四个排查阶段、误判在哪、最终方案+三条教训 |
| `docs/su-playbook.md` | **su 打通步骤**（四条件 + 6 步 + 排错表） |
| `docs/status_20261024.md` | 现状快照：版本/ko/已验证项/剩余项 |
| `docs/progress_20261024.md` | 当天全过程 + 结论修正 |
| `docs/progress_20261022.md` / `20261021.md` | 早期链路：双 kprobe sucompat、MTE 去标签 |
| `docs/new-device-checklist.md` | 同型号同固件机器的直用清单 |

## 文档

- [2026-10-25 panic 排查 / sucompat 默认关闭](docs/status_20261025.md)
