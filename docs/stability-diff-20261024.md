# 2.8「内核不稳定」排查：与老壳 3.16.34 的差异 + 2.10 优化

> 对象：`m6-shell 2.8`（新壳）vs `一键越狱 3.16.34`（老壳，`com.dsh.ppgroot` vc332，
> md5 `c1c878d20954659b5de04d574c8885e3`）。
> 目标：找出 2.8 上「系统不稳」的来源，逐项消除，同时保住 su 修复。

## 一、结论

四个差异，按嫌疑排序：

| # | 差异 | 证据 | 嫌疑 |
|---|---|---|---|
| 1 | **ko 不同**：2.8 默认 FinaSU **v1.0.9**，老壳从未内置 finasu/sukisu | 老壳内置 8 份 ko；v1.0.9 新增 `mount_view_filter`（hook `seq_show`/`open`，+470 行） | ★★★ 最可能 |
| 2 | **App 侧轮询**：2.8 每秒最多 6 次 `newProcess` | `watch()` 每轮：`wc`×2 + `tail`×2 + `cat` + 扫 `/proc/modules`，`POLL_MS=1000`，跑 8 分钟 | ★★★ 很可能（发烫/卡顿/看门狗） |
| 3 | **loader 后台 watcher**：2.8 装载后 fork 一个每秒读 `/proc/modules` 的循环（≤180s） | 老壳完全没有这段 | ★★ 次要（且实测是死代码） |
| 4 | 细节：`mgr_pkgs` 多传 2 个包、`/data/adb/ksud` 提前铺 | 老壳候选 5 个，2.8 是 7 个（多了 `com.sukisu.ultra`、`me.yuki.fina`） | ★ 很小 |

**loader 本体几乎一致**：函数集合只差 `_self_apk_path` / `say`，参数组装 `build_ksu_params` 逐行对比只多一条
「DYNAMIC_MGR 时传 `mgr_dont_invalidate=0`」。所以不稳定**不是**脚本逻辑差异造成的。

## 二、逐项证据

### 1. ko 集合

```
老壳 3.16.34 内置（8 份，全是 uapi ≤ 5）：
  10,321,384  kernelsu_custom.ko
   6,355,480  kernelsu_next.ko
   6,068,248  kernelsu_ksu331.ko
   5,540,360  kernelsu_cga.ko
   5,533,360  kernelsu-os9-selx11.ko
   5,459,384  kernelsu_success.ko
     404,928  kernelsu-android16-6.12.ko
     330,664  kernelsu.ko

2.8 内置（7 份，含两份 uapi6）：
   10.3 MB  kernelsu_custom.ko（BakaSU main）
    6.1 MB  kernelsu_ksu331.ko
    6.4 MB  kernelsu_next.ko
  5,533,360 kernelsu-os9-selx11.ko
  5,459,384 kernelsu_success.ko
  7,465,008 kernelsu_sukisu.ko   ← uapi6，老壳没有
  7,489,752 kernelsu_finasu.ko   ← uapi6 / v1.0.9，老壳没有
```

FinaSU v1.0.9 相对 v1.0.8 的改动（`compare/v1.0.8...v1.0.9`）：

```
kernel/feature/mount_view_filter.c   +470
kernel/feature/mount_view_filter.h     +7
kernel/core/init.c                     +3
kernel/policy/feature.c                +1
kernel/Kbuild                          +1
uapi/feature.h                         +1
```

`mount_view_filter` 挂的是**挂载视图过滤**——`seq_show` / `open` 这些**全局热路径**，
每次读 `/proc/self/mounts`、每次路径解析都可能过它。结构体偏移一旦与厂商内核不符，
不是在某个角落报错，而是全系统一起抖。这与「模块 Live、su 能用，但系统不稳/偶发 exec 段错误」的画像吻合。

### 2. App 侧轮询（实测数字）

`ShizukuBridge.shell()` 每次调用 = `newProcess(cmd)` = **一次 app_process 冷启动**。

2.8 的 `watch()` 每轮（1 秒）：

| 调用 | 次数 |
|---|---|
| `readNew(dc_ksu.log)` → `wc -c` + `tail -c` | 2 |
| `readNew(run.stdout.log)` → `wc -c` + `tail -c` | 2 |
| `cat dc_ksu.log`（再读整个日志用于判定） | 1 |
| `moduleLoaded()` 扫 `/proc/modules` | 1 |
| **合计** | **6 次/秒** |

跑满 8 分钟 ⇒ 约 **2,880 次**进程生成。这台机本来内存就紧（16 GB，可用常年在 1~2 GB），
每秒 6 个 `app_process` 足以造成发热、掉帧，严重时触发系统的看门狗动作。

### 3. loader 后台 watcher

2.8 装载后 fork：

```sh
RD=... ORIG_ENF=... setsid sh -c '... while ...; sleep 1 ...' &
```

每秒读 `/proc/modules`，最多 180 秒。实测它是**死代码**——子进程继承同一份伪造 cred，
模块一装就一起失效，日志只写出第一行。老壳完全没有这段。

## 三、2.10 做的四件事

| 改动 | 位置 | 效果 |
|---|---|---|
| ① `watch()` 合并成**一次** Shizuku 调用（模块状态 + 两个日志增量，用 `@@MOD/@@S1/@@S2/@@D1/@@D2` 分隔） | `JailbreakShell.kt` | 6 次/秒 → **0.5 次/秒**（`POLL_MS` 1000→2000） |
| ② 判定串改用**累积缓冲**，不再每轮 `cat` 整个日志 | 同上 | 去掉 1 次/秒的整文件读取 |
| ③ loader 后台 watcher **默认关闭**（要开：`touch $RD/POSTLOAD_WATCHER`） | `ksu_loader.sh` | 去掉一个装载后的常驻循环 |
| ④ FinaSU v1.0.9 启动时**显式告警**；`touch $RD/PREFER_STABLE_KO` 可自动改用 SukiSU ko | `ksu_loader.sh` | 把「不稳定」和「v1.0.9」在日志里绑在一起，便于判定 |

保留不变的：su 落点三级（`/dev/su` → `/data/app/.ksu-payload` → app lib）、`su_cred=2`、
`su_path` 传参、管理器选择、ko 修补链路。

## 四、想更稳的两个方向（未做，需真机对比）

1. **换 ko**：`me.yuki.fina` 管理器配 **FinaSU v1.0.8 ko**（无 `mount_view_filter`），
   或直接把管理器换成 **SukiSU v4.2.1-r1**（uapi6，无该功能）。两者都验证过 su 可用。
2. **退回 uapi5**：BakaSU `kernelsu_custom.ko`——老壳的默认线，uapi 5 生态最成熟。

## 五、回退方式

| 想回到 | 做法 |
|---|---|
| 旧轮询行为 | 2.10 之前是 6 次/秒，不建议；只想关日志刷新可在 `LogActivity` 退出后停 |
| 旧 watcher | `touch /data/local/tmp/route/m6/POSTLOAD_WATCHER` |
| FinaSU v1.0.8 ko | 替换 `payload/kernelsu_finasu.ko`（7,359,024 B `9590c0b0a9c6bbba758a91c69c130f45`）后重打包 |
| SukiSU 线 | 管理器装 SukiSU v4.2.1-r1，`printf 'com.sukisu.ultra' > $RD/MANAGER_PKG` |

## 六、产物

```
m6-shell-2.10-release.apk   23,029,329 B   md5 5463bc9d19e826fe20adc216772a3e93   vc20
载荷 ksu_loader.sh           md5 e642b5c8924abe2b338782e0aa37040d
```

**2.10 尚未真机验证**：装机后看两件事 ——
① 提权流程是否照常（"已观察 Ns" 心跳变成 30s 一次属正常）；
② 长时间使用是否还发烫/卡顿。
