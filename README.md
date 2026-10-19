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
