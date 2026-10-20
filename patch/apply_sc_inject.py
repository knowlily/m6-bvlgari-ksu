# -*- coding: utf-8 -*-
"""把「注入 sys_call_table / ni_syscall + 调度器未装时不改写 syscallno」打进 KernelSU 源码。
幂等：已打过就直接退出 0。容忍 CRLF/LF。找不到锚点也退出 0（不阻塞出 ko）。
用法: python3 apply_sc_inject.py <KernelSU 源码目录>
"""
import os
import re
import sys

MARK = "ksu_sc_table_addr"


def load(path):
    with open(path, "rb") as f:
        raw = f.read()
    crlf = b"\r\n" in raw
    return raw.decode("utf-8", "surrogateescape").replace("\r\n", "\n"), crlf


def save(path, text, crlf):
    if crlf:
        text = text.replace("\n", "\r\n")
    with open(path, "wb") as f:
        f.write(text.encode("utf-8", "surrogateescape"))


def patch_hook(root):
    p = os.path.join(root, "kernel/hook/arm64/syscall_hook.c")
    if not os.path.exists(p):
        return "skip: no file"
    s, crlf = load(p)
    if MARK in s:
        return "already"
    hit = 0

    old0 = "syscall_fn_t *ksu_syscall_table = NULL;\nint ksu_dispatcher_nr = -1;"
    if old0 in s:
        s = s.replace(old0, old0 + """

/* 2026-10-20（BVL 6.1.157）：按名解析 sys_call_table / ni_syscall 会失败，
 * 改由 loader 从 /proc/kallsyms 现算后经模块参数注入（KASLR 每次开机都变）。 */
static unsigned long ksu_sc_table_addr = 0;
module_param_named(sc_table, ksu_sc_table_addr, ulong, 0644);
static unsigned long ksu_sc_ni_addr = 0;
module_param_named(sc_ni, ksu_sc_ni_addr, ulong, 0644);""", 1)
        hit += 1

    m = re.search(r'\n([ \t]*ni_syscall = \(unsigned long\)ksu_resolve_symbol_for_functable_hook\("__arm64_sys_ni_syscall"\);)', s)
    if m:
        s = s.replace(m.group(1), m.group(1) + """
    if (!ni_syscall && ksu_sc_ni_addr)
        ni_syscall = ksu_sc_ni_addr;""", 1)
        hit += 1
    s = s.replace('pr_info("sys_ni_syscall: 0x%lx\\n", ni_syscall);',
                  'pr_alert("ksu: sys_ni_syscall: 0x%lx\\n", ni_syscall);', 1)

    t = 'ksu_syscall_table = (syscall_fn_t *)ksu_resolve_symbol_for_functable_hook("sys_call_table");'
    if t in s:
        s = s.replace(t, t + """
    if (!ksu_syscall_table && ksu_sc_table_addr)
        ksu_syscall_table = (syscall_fn_t *)ksu_sc_table_addr;""", 1)
        hit += 1
    s = s.replace('pr_info("sys_call_table=0x%lx", (unsigned long)ksu_syscall_table);',
                  'pr_alert("ksu: sys_call_table=0x%lx (injected=%d)\\n", (unsigned long)ksu_syscall_table, (int)(ksu_sc_table_addr != 0));', 1)
    s = s.replace('pr_info("dispatcher installed at slot %d\\n", ksu_dispatcher_nr);',
                  'pr_alert("ksu: dispatcher installed at slot %d\\n", ksu_dispatcher_nr);', 1)

    anchor = "static long __nocfi ksu_syscall_dispatcher(const struct pt_regs *regs)\n{\n"
    if anchor in s:
        s = s.replace(anchor, anchor + """    {
        static int ksu_dbg_disp_cnt;
        if (ksu_dbg_disp_cnt < 20) {
            ksu_dbg_disp_cnt++;
            pr_alert("ksu-dbg DISP: syscallno=%d disp=%d x8=%lu\\n", regs->syscallno, ksu_dispatcher_nr,
                     (unsigned long)PT_REGS_ORIG_SYSCALL(regs));
        }
    }
""", 1)
        hit += 1

    if hit == 0:
        return "skip: anchors not found"
    save(p, s, crlf)
    return "patched(%d)" % hit


def patch_mgr(root):
    p = os.path.join(root, "kernel/hook/syscall_hook_manager.c")
    if not os.path.exists(p):
        return "skip: no file"
    s, crlf = load(p)
    if "skip redirect" in s:
        return "already"
    old = "    if (ksu_has_syscall_hook(id)) {\n        struct pt_regs *current_regs = task_pt_regs(current);\n"
    if old not in s:
        return "skip: anchor not found"
    s = s.replace(old, old + """        /* 2026-10-20：调度器没装上时绝不可改写 syscallno —— 否则 -1 落到
         * do_ni_syscall，全系统这几个系统调用直接 ENOSYS（BVL 实际事故）。 */
        if (ksu_dispatcher_nr < 0) {
            pr_warn_once("ksu: dispatcher not installed, skip redirect (nr=%ld)\\n", id);
            return;
        }
        {
            static int ksu_dbg_enter_cnt;
            if (ksu_dbg_enter_cnt < 20) {
                ksu_dbg_enter_cnt++;
                pr_alert("ksu-dbg ENTER: id=%ld disp=%d syscallno=%d x8=%lu\\n", id, ksu_dispatcher_nr,
                         current_regs->syscallno, (unsigned long)PT_REGS_ORIG_SYSCALL(current_regs));
            }
        }
""", 1)
    save(p, s, crlf)
    return "patched"


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print("[sc-inject] hook :", patch_hook(root))
    print("[sc-inject] mgr  :", patch_mgr(root))
    sys.exit(0)
