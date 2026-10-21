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

    # 2026-10-21：phys_to_virt / PAGE_MASK 需要 mm.h + asm/memory.h
    if "asm/memory.h" not in s and '#include "klog.h"' in s:
        s = s.replace('#include "klog.h"',
                      '#include "klog.h"\n#include <linux/mm.h>\n#include <asm/memory.h>', 1)
        hit += 1

    # TODO: prototypes for the alternate write path
    if "set_memory_rw(" not in s:
        s = s.replace("syscall_fn_t *ksu_syscall_table = NULL;",
                      """/* 2026-10-20: alternate write path (fixmap write of sys_call_table has no effect on Honor) */
extern int set_memory_rw(unsigned long addr, int numpages);
extern int set_memory_ro(unsigned long addr, int numpages);
extern int ksu_handle_setresuid(uid_t old_uid, uid_t new_uid);
static void ksu_sp_hooks_init(void);

syscall_fn_t *ksu_syscall_table = NULL;""", 1)
        hit += 1

    old0 = "syscall_fn_t *ksu_syscall_table = NULL;\nint ksu_dispatcher_nr = -1;"
    if old0 in s:
        s = s.replace(old0, old0 + """

/* 2026-10-20（BVL 6.1.157）：按名解析 sys_call_table / ni_syscall 会失败，
 * 改由 loader 从 /proc/kallsyms 现算后经模块参数注入（KASLR 每次开机都变）。 */
static unsigned long ksu_sc_table_addr = 0;
module_param_named(sc_table, ksu_sc_table_addr, ulong, 0644);
static unsigned long ksu_sc_ni_addr = 0;
module_param_named(sc_ni, ksu_sc_ni_addr, ulong, 0644);

/* kimage_voffset 的「符号地址」（不是值）。loader 从 /proc/kallsyms 取地址传进来，
 * 内核里解引用即得 KASLR 后镜像的 VA→PA 偏移，用来校正页表走查算出的物理地址。 */
static unsigned long ksu_kimg_voff_addr = 0;
module_param_named(kimg_voff, ksu_kimg_voff_addr, ulong, 0644);""", 1)
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
    # 回读校验：patch 是否真的落进 sys_call_table（Honor 内核常写到别名页/只读副本）
    s = s.replace('pr_alert("ksu: dispatcher installed at slot %d\\n", ksu_dispatcher_nr);',
                  'pr_alert("ksu: dispatcher installed at slot %d readback=%px want=%px\\n", ksu_dispatcher_nr, (void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]), (void *)ksu_syscall_dispatcher);', 1)
    # 安全阀 + 备用写入路径：patch 没落进表时，改用 set_memory_rw 直写再回读
    s = s.replace('''(void *)ksu_syscall_dispatcher);''',
                  '''(void *)ksu_syscall_dispatcher);
    if ((void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]) != (void *)ksu_syscall_dispatcher) {
        unsigned long _pg = (unsigned long)&ksu_syscall_table[ksu_dispatcher_nr] & PAGE_MASK;
        int _rc = set_memory_rw(_pg, 1);
        pr_alert("ksu: rw-path set_memory_rw(%px)=%d\\n", (void *)_pg, _rc);
        if (_rc == 0) {
            WRITE_ONCE(ksu_syscall_table[ksu_dispatcher_nr], (syscall_fn_t)ksu_syscall_dispatcher);
            set_memory_ro(_pg, 1);
            pr_alert("ksu: rw-path after write slot=%d val=%px\\n", ksu_dispatcher_nr,
                     (void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]));
        }
        if ((void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]) != (void *)ksu_syscall_dispatcher) {
            /* 2026-10-21 第三个写入口：线性别名（phys_to_virt）。
             * 前两条都失败 ⇒ 问题在「物理地址算错」或「fixmap 映射只读」。
             * 这里把页表走查值和 kimage_voffset 校正值都打出来，失败也知道是哪一种。 */
            unsigned long _va = (unsigned long)&ksu_syscall_table[ksu_dispatcher_nr];
            int _e1 = 0;
            unsigned long _ph = phys_from_virt(_va & PAGE_MASK, &_e1);
            unsigned long _ph_k = 0;
            if (ksu_kimg_voff_addr) {
                unsigned long _voff = *(unsigned long *)ksu_kimg_voff_addr;
                if (_voff)
                    _ph_k = _va - _voff;
            }
            pr_alert("ksu: phys walk=0x%lx err=%d kimg=0x%lx\\n", _ph, _e1, _ph_k);
            if (_ph_k)          /* image 内地址：kimage_voffset 绝对值更可信 */
                _ph = _ph_k;
            if (_ph) {
                unsigned long _la = (unsigned long)phys_to_virt(_ph & PAGE_MASK) | (_va & ~PAGE_MASK);
                pr_alert("ksu: linear alias=%px pa=0x%lx\\n", (void *)_la, _ph);
                WRITE_ONCE(*(syscall_fn_t *)_la, (syscall_fn_t)ksu_syscall_dispatcher);
                pr_alert("ksu: linear after write slot=%d val=%px\\n", ksu_dispatcher_nr,
                         (void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]));
            }
        }

        if ((void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]) != (void *)ksu_syscall_dispatcher) {
            pr_alert("ksu: slot patch NOT effective -> redirect disabled (safe mode)\\n");
            ksu_dispatcher_nr = -1;
        } else {
            pr_alert("ksu: rw-path OK, dispatcher active\\n");
        }
    }''', 1)

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

    # ── patch_memory.c：确认 fixmap 写入到底有没有落到物理页 ──
    pm = os.path.join(root, "kernel/hook/arm64/patch_memory.c")
    if os.path.exists(pm):
        sm, cm = load(pm)
        if "fixmap_read" not in sm:
            old_pm = "    ret = (int)copy_to_kernel_nofault(map, src, len);"
            if old_pm in sm:
                sm = sm.replace(old_pm, old_pm + '''
    pr_alert("ksu: patch dst=%px len=%zu ret=%d fixmap_read=%px\\n", dst, len, ret, *(void **)map);''', 1)
                save(pm, sm, cm)
                hit += 1

    # ── reboot 诊断：谁在重启设备（Honor bootfail 会 30-60s 自动重启）──
    pb = os.path.join(root, "kernel/supercall/supercall.c")
    if os.path.exists(pb) and "ksu-dbg REBOOT" not in open(pb, encoding="utf-8", errors="replace").read():
        sb, cb = load(pb)
        old_r = """    if (magic1 == KSU_INSTALL_MAGIC1 && magic2 == KSU_INSTALL_MAGIC2) {"""
        if old_r in sb:
            sb = sb.replace(old_r, """    {
        static int _ksu_rb_cnt;
        if (_ksu_rb_cnt < 10) {
            _ksu_rb_cnt++;
            pr_alert("ksu-dbg REBOOT: pid=%d comm=%s magic=%x/%x\\n",
                     current->pid, current->comm, magic1, magic2);
        }
    }
""" + old_r, 1)
            save(pb, sb, cb)
            hit += 1
    # ── setresuid kprobe：绕开死掉的 sys_call_table dispatcher ──
    #   为什么要它：管理器拿内核 fd 的唯一入口是 ksu_handle_setresuid()
    #   （zygote fork 后 setresuid 时把 [ksu_driver] fd 塞进管理器进程）。
    #   dispatcher 死了 ⇒ 这条永远不跑 ⇒ 管理器 scan_driver_fd() 扫不到
    #   ⇒ isManager=false ⇒ ksuVersion=null ⇒ 首页显示「未安装」。
    if "ksu_sp_setresuid_kp" not in s:
        s = s.rstrip() + '''

/* 2026-10-21: setresuid kprobe —— sys_call_table dispatcher 在本机不可用
 * （fixmap 写不进去 / set_memory_rw=-22），改用 kprobe 走同一条业务逻辑。
 * 管理器进程拿到 [ksu_driver] fd 全靠这里。 */
#include <linux/kprobes.h>
#include <linux/cred.h>
#include <linux/sched.h>

extern int ksu_handle_setresuid(uid_t old_uid, uid_t new_uid);

static int ksu_sp_setresuid_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct pt_regs *real = (struct pt_regs *)regs->regs[0];
    unsigned int euid;
    if (!real)
        return 0;
    euid = (unsigned int)real->regs[1];
    if (euid == 0xffffffffu)
        return 0;
    ksu_handle_setresuid(current_uid().val, (uid_t)euid);
    return 0;
}

static struct kprobe ksu_sp_setresuid_kp = {
    .symbol_name = "__arm64_sys_setresuid",
    .pre_handler = ksu_sp_setresuid_pre,
};

static void ksu_sp_hooks_init(void)
{
    int rc = register_kprobe(&ksu_sp_setresuid_kp);
    pr_alert("ksu: setresuid kprobe rc=%d\\n", rc);
}
'''
        s = s.rstrip() + "\n"
        # 我们的块必须落在 __aarch64__ 守卫内：把原来的 #endif 挪到文件尾
        if "#endif /* __aarch64__ */" in s:
            s = s.replace("#endif /* __aarch64__ */", "", 1)
            s = s.rstrip() + "\n#endif /* __aarch64__ */\n"
        hit += 1

    # 在 init 末尾调用 kprobe 注册
    if "ksu_sp_hooks_init();" not in s:
        s = s.replace('''        pr_alert("ksu: rw-path OK, dispatcher active\\n");
        }''', '''        pr_alert("ksu: rw-path OK, dispatcher active\\n");
        }
    ksu_sp_hooks_init();''', 1)
        if "ksu_sp_hooks_init();" in s:
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
    old_dual = "        PT_REGS_ORIG_SYSCALL(current_regs) = id;\n        current_regs->syscallno = ksu_dispatcher_nr;"
    new_dual = (old_dual
                + "\n        /* 2026-10-20 dual write: callback regs may differ from task_pt_regs */"
                + "\n        PT_REGS_ORIG_SYSCALL(regs) = id;"
                + "\n        ((struct pt_regs *)regs)->syscallno = ksu_dispatcher_nr;")

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
        {
            static int ksu_dbg_ptr_cnt;
            if (ksu_dbg_ptr_cnt < 5) {
                ksu_dbg_ptr_cnt++;
                pr_alert("ksu-dbg PTR: cb=%px task=%px same=%d\\n", (void *)regs, (void *)current_regs,
                         (int)((void *)regs == (void *)current_regs));
            }
        }
""", 1)
    if old_dual in s:
        s = s.replace(old_dual, new_dual, 1)
    save(p, s, crlf)
    return "patched"


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print("[sc-inject] hook :", patch_hook(root))
    print("[sc-inject] mgr  :", patch_mgr(root))
    sys.exit(0)
