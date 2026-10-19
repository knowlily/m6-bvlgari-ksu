#!/usr/bin/env python3
"""为设备内核「未导出」的符号生成 kallsyms 运行时解析垫片（KernelSU 用）。

设备内核实测：只有 kallsyms_lookup_name / register_kprobe 在 __ksymtab 里，
path_umount、selinux_state、alloc_uid、init_mm 等 60+ 符号「符号存在但没导出」。
官方 KSU 直接 extern 调用 ⇒ modpost undefined + insmod 时 Unknown symbol。

本脚本生成 ksu_symshim.c：
  - 文本符号(T/t)：汇编跳板，调用同名符号 → 跳到运行时解析到的地址。
    不碰 x0-x7，所以无需知道原型；br x16 也是 BTI 合法的间接跳转形式。
  - 数据符号(D/d/B/b)：打印出来，必须源码级改成指针（脚本不自动改）。

用法:
  python3 gen_shim.py undef.txt types.txt > ksu_symshim.c
    undef.txt  每行一个未定义符号
    types.txt  每行 "符号 类型"（/proc/kallsyms 第 2 列）
"""
import sys


def load_types(p):
    m = {}
    for line in open(p, encoding="utf-8", errors="ignore"):
        parts = line.split()
        if len(parts) == 2:
            m[parts[0]] = parts[1]
    return m


def main():
    undef = [l.strip() for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
    types = load_types(sys.argv[2])
    text, data = [], []
    for s in undef:
        t = types.get(s, "T")
        (data if t in "DdBbRr" else text).append((s, t))

    o = []
    o.append("// 自动生成：设备内核未导出符号的运行时解析垫片")
    o.append("#include <linux/kallsyms.h>")
    o.append("#include <linux/module.h>")
    o.append("")
    for s, _ in text:
        o.append(f"unsigned long ksu_addr_{s};")
    o.append("")
    for s, _ in text:
        o.append(
            f'asm(".globl {s}\\n{s}:\\n\\tadrp x16, ksu_addr_{s}\\n'
            f'\\tldr x16, [x16, #:lo12:ksu_addr_{s}]\\n\\tbr x16\\n");'
        )
    o.append("")
    o.append("int __init ksu_symshim_setup(void)")
    o.append("{")
    o.append("    int miss = 0;")
    for s, _ in text:
        o.append(f'    ksu_addr_{s} = (unsigned long)kallsyms_lookup_name("{s}");')
        o.append(f'    if (!ksu_addr_{s}) {{ pr_warn("ksu-shim: 解析不到 {s}"); miss++; }}')
    o.append('    pr_info("ksu-shim: %d 个跳板就绪，%d 个失败\\n", '
             f'{len(text)}, miss);')
    o.append("    return miss;")
    o.append("}")
    sys.stderr.write(f"文本符号 {len(text)} 个；数据符号 {len(data)} 个\n")
    for s, t in data:
        sys.stderr.write(f"  [数据] {s} ({t}) —— 需要源码改成指针\n")
    print("\n".join(o))


if __name__ == "__main__":
    main()
