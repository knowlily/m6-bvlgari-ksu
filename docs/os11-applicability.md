# 本仓库文档在 OS11 上的可用性审计

> 结论先说：**这些文档是 MagicOS 10（内核 `6.1.157-android14-11-g7d3922fade43`）的，不能整份照搬到 OS11。**
> OS11 是 `6.1.157-android14-11-g84ffa64b710b` —— 版本号同为 6.1.157，**build token 不同**，
> 所以 vermagic、符号地址、内核偏移全部对不上；提权原语也换了一代。
>
> 审计日期：2026-10-29。OS11 一侧的最新事实见
> `m6-jailbreak-core/docs/08-os11-route.md`、`m6-os11-ksu/docs/managers-compat-20261029.md`、
> `m6-os11-exploit/docs/plan-64560-os11.md`。

## 一、逐篇结论

图例：**A 可直接用**（与内核 build 无关的方法论）/ **B 需替换参数**（结论仍对，值要重采）/ **C 仅存档**（OS11 上已证伪或形态不同）

| 文档 | 结论 | 说明 |
|---|---|---|
| `new-device-checklist.md` | **B** | 换机流程本身可复用；里面的内核串 `g7d3922fade43`、233 条静态符号、ko 名单全部要换。OS11 用 `next_sym_addr_11_110.txt` + 2 参 `ko_patch2` |
| `su-playbook.md` | **B** | su 的四道闸门（文件在不在 / hook 放不放 / 策略 allow / cred 真不真）是通用排查顺序；但「su 由 `su_path` 注入真文件、与内核 sucompat 无关」这条是 OS10 的实测结论，OS11 的 1.1.3 线走的是 `allow_shell=1` + ko 自带 sucompat，两类机制并存，上机要重新验 |
| `su-完整技术报告.md` | **B** | 同上，机制分析可读，结论按档重验 |
| `su-问题解决记录.md` | **B** | 症状库仍然有用（假阴性、`[ -x ]` 骗人）；具体路径按档换 |
| `stability-diff-20261024.md` | **C** | 对比的是 OS10 两个版本的稳定性差异，OS11 无对应数据 |
| `status_20261024.md` / `status_20261025.md` | **C** | OS10 阶段状态快照，只作历史 |
| `status_20261027_os10-final.md` | **C** | OS10 结案报告（成功率 ≈56%，瓶颈在 exploit 二进制内部）。**OS11 不继承这个成功率**，因为原语换了 |
| `progress_2026102*.md`（21~28） | **C** | 逐日过程记录。里面的踩坑条目有价值，但每条都要在 OS11 上重新判真伪 —— 已经出现过「OS10 的结论在 OS11 反向」的例子（见 P31 与下节 43499） |

## 二、OS10 结论在 OS11 上明确失效的三条

### 1. CVE-2026-43499 的写原语用法（最要紧）

OS10 这条线（mm_struct + skb spray、owner 持锁形态）在 OS11 上**不能照搬**：
`pi_blocked_on` 的三条清理路径（requeue 代拿锁 / 醒来抢锁 / 超时 `remove_waiter`）我们已在 OS11 内核上
反汇编 + 探针双证，全部清 0（见 `m6-os11-exploit/docs/progress_20261029.md`）。

但**不要因此认为 43499 在 OS11 上完全不可用**：`荣耀Magic6Pro提权 1.1.3` 的 `profile.conf` 记录
L3（GL3 pwn，futex PI 链）在本机 uptime 226s 时 3 秒命中、`uid=0`、SELinux 保持 Enforcing。
差别在**调用形态**：它的 `thread_w` 是「自己先 `FUTEX_LOCK_PI` 再 `WAIT_REQUEUE_PI`」，
走 EDEADLK 死锁检测分支（`remove_waiter` 清的是 requeuer），不是 OS10 那套 owner 持锁。

⇒ 本仓库的 43499 章节只当「OS10 当时的用法」看；OS11 要按 `08-os11-route.md` 的 L3 形态。

### 2. KASLR 的拿法

OS10：3 参 `ko_patch2` + 静态符号表反推 slide。
OS11：**2 参** `ko_patch2 <ko> /proc/kallsyms` 直读 live 地址；
另有不依赖漏洞的 tracefs 投票法（`m6-os11-exploit/tools/kaslr_trace.py`，
实测 `_text=0xffffffd217c00000`）。用 OS10 那套反推，233 条地址会全错 —— 旧版正是这么栽的。

### 3. ko 与 uapi 的配对

OS10 阶段认为「v325 那份 kprobe-sucompat ko 是风险最低的兜底」。
按源码核实（`uapi/supercall.h` 的 `KERNEL_SU_UAPI_VERSION`），**v3.2.5 = uapi 2**，
只配它自带的 3.2.5 管理器；装 3.3.1 / KSU-Next / 任何 uapi6 管理器都会
`UAPI version mismatch`，症状是「模块 Live 但管理器连不上」。

⇒ 一律**一个管理器一份同代 ko**，选型真源 `m6-os11-exploit/chain/manager_select.sh`。

## 三、仍然通用、可以放心沿用的部分

| 内容 | 为什么通用 |
|---|---|
| B 路线编译法（开着 `MODVERSIONS` 编、编完清 `__versions` 段，vermagic 保留 `modversions` token） | 与内核版本无关，是本机厂商内核唯一可行的路线 |
| vermagic **整串**校验（含 `SMP preempt mod_unload modversions aarch64`） | OS11 同样适用，只换 build token |
| 只编 `vmlinux` + `kernelsu/` 两步 | 省 20~40 分钟，通用 |
| 装载顺序：**拿 KASLR → 触发拿 uid=0 → 借中继做前置 → `ko_patch2` → `insmod` → `kill -9`** | 装载后中继必失权、`insmod` 后参数改不了，这两条物理约束不变 |
| 判据：`/proc/modules` 有 `kernelsu … Live`，**充分判据是 `/dev/ksu` 存在** | 半初始化（模块 Live 但 `/dev/ksu` 缺）在 OS11 同样出现 |
| 装载后 `[ -x ]` / `stat` / `exec` 大面积假阴性 | 钩子生效的必然结果，与内核版本无关 |
| 看门狗：`rmmod core_hang_detect` / `gh_virt_wdt`，真开关是 `hang_detect_core/enable` | 路径未变（OS11 实测前两个 `cat` 被 SELinux 挡，权限要重测） |
| 单 boot 一发（`run.lock` 按 boot_id），命中后立刻 `kill -9` | 原语污染内核状态，跨 boot 才能清 |
| 管理器加冕的坑（`track_throne` 按 `packages.list` 顺序挑错人） | 与内核版本无关 |

## 四、OS11 与 OS10 的关键差异（速查）

| 项 | OS10 | OS11 |
|---|---|---|
| 内核 build | `6.1.157-…-g7d3922fade43` | `6.1.157-…-g84ffa64b710b` |
| ko_patch2 | 3 参（静态符号表反推 slide） | **2 参**（`<ko> /proc/kallsyms`） |
| 符号清单 | `next_sym_addr_uapi5.txt`（233 条） | `next_sym_addr_11_110.txt` |
| 开火窗口 | `[90, 300]` | `[240, 86400]` |
| 落地标志 | SELinux → Permissive | 直接 `uid=0`，SELinux 保持 Enforcing |
| root 通道 | exploit exec `ksu_loader.sh` | `pwn` 的中继 `.mc_cmd` / `.mc_out` |
| 提权原语 | 43499（owner 持锁 + skb spray） | 43499 的 GL3 形态（waiter 先持锁 / EDEADLK）或 **CVE-2026-64560** |
| 管理器 | FinaSU 1.0.x / SukiSU 4.2.1-r1 | 官方 3.3.1 / KSU-Next 3.4.2 / BakaSU 4.2.0-rc3 / FinaSU 1.3.0 / SukiSU 4.3.0（一管理器一份 ko） |

## 五、使用建议

1. **要上手 OS11，别从这里开始**：先读 `m6-jailbreak-core/docs/08-os11-route.md`。
2. 来这里查的是**方法论和症状库**（su 四道闸门、假阴性、`track_throne` 挑错人、装载顺序），
   不是参数值。
3. 任何一条 OS10 结论搬到 OS11 前，先问：它依赖的是**内核 build**（⇒ 要重采）
   还是**装载/判据/流程**（⇒ 多半通用）。
