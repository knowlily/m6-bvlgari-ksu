# OS10 阶段结案（2026-10-27）

MagicOS 10（`6.1.157-android14-11-g7d3922fade43` / Android 16 / 170-175 固件）的研究**到此截止**。
不是失败收尾 —— 是「已经打通、可用，但稳定性的瓶颈不在我们能改的那一层」，所以停止投入，转到 os11。

## 结果一句话

BL 未解锁、不改系统镜像的前提下，运行时拿到 root 并成功装载 `kernelsu*.ko`；
**成功率 ≈ 56%（10 成 / 8 败，18 轮真机日志）**，失败全部卡在 exploit 二进制内部。

## 本轮之后的定稿

| 项 | 定稿值 |
|---|---|
| APK | `m6-shell` 2.92（vc103），11 个 ko 全部内嵌，按管理器自动选 |
| exploit | `cve-2026-43499-bin-dc14`，160,240 B，md5 `6ce02b825fe5c04b7323cfa2317d0e01` |
| 首败处置 | 立刻 SIGKILL + 写 `NEED_REBOOT`（带签名）；**换新进程重试已证明无效，默认关** |
| 安静窗口 | 开（`am kill-all` + CPU 连续 3s 空闲 ≥65%） |
| 停滞看门狗 | 开（`before-slide-route` 15s / 其它 60s），已救下两次 |
| su 通路 | `/dev/su` + `su_cred=2`，`force_feature=su_compat:0`（sucompat 默认关） |
| 统计 | App 内成功率 / 复位次数；失败轮靠 `run_pending.txt` 在下一次启动补记 |

## 三条硬结论

1. **第一次 slide 决定成败**：10 份成功日志全是 `attempt=1/8 verified=1 step=0`，attempt 2~8 从未成功。
2. **失败是 boot 级，不是进程级**：换全新进程重试两次，倒在同一个签名。
3. **`soft-fail` / `cannot place` 是噪声**：成功轮里也大量出现，真信号是 `verified=0/1 step=N`。

## 未解决（交给 os11，不在 OS10 上继续）

- `step=4`：p0 写没落到 `ashmem_miscs.fops`（回读 `val=0`）
- `step=8`：physrw 没立起来，随后紧跟整机复位
- `step=1 errno=34`：CFI oracle 在 6.1.157 上算偏（ERANGE）
- 日志停在 `before-slide-route`：内核 rt_mutex 链死锁，用户态无解

这四条都在二进制里；脚本层剩下的只有 `PSELECT_DELAY_USEC` / `STACK_WRITER_DELAY_USEC` 的时序 A/B，
而 os11 用的是另一套弹药（L3/GL3 futex-PI + epoll spray），所以不值得在 OS10 上继续。

## 完整归档

`m6-jailbreak-core/docs/09-os10-archive.md` —— 结论、数据、默认参数表、ko 映射、12 条铁律、复现步骤、文件索引。

## 下一站

`m6-jailbreak-core/docs/08-os11-route.md` —— MagicOS 11（110 固件，`…g84ffa64b710b`）走 `payload/os11/`，
装载以后仍复用主线 `ksu_loader.sh`（只有「打内核」这一段换人）。
