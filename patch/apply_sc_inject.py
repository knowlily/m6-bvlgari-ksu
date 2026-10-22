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
                      '#include "klog.h"\n#include <linux/mm.h>\n#include <linux/err.h>\n#include <linux/vmalloc.h>\n#include <asm/memory.h>', 1)
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
    # ── 回读 + 兜底写入全部收敛到 ksu_force_patch_table() ──
    orig_print = 'pr_info("dispatcher installed at slot %d\\n", ksu_dispatcher_nr);'
    if orig_print in s:
        s = s.replace(orig_print,
                      'pr_alert("ksu: dispatcher installed at slot %d readback=%px want=%px\\n", ksu_dispatcher_nr, (void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]), (void *)ksu_syscall_dispatcher);\n'
                      '    ksu_force_patch_table();\n'
                      '    ksu_sp_hooks_init();', 1)
        hit += 1

    # 兜底写入函数（文件作用域）：单独放 patch/force_patch_table.c，避免在 init 里堆嵌套 if
    if "ksu_force_patch_table(void)" not in s.split("ksu_syscall_hook_init(void)")[0]:
        helper = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "force_patch_table.c"),
                      encoding="utf-8").read()
        k = s.find("ksu_syscall_hook_init(void)")
        if k > 0:
            ls = s.rfind("\n", 0, k) + 1     # 该函数签名行行首（可能是 void __init ...）
            s = s[:ls] + helper + "\n" + s[ls:]
            hit += 1

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
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
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

/* 2026-10-21: su 重定向 —— 把 execve("/system/bin/su") 换成 App 自己 lib 目录里的
 * ksud 副本（标签 apk_data_file，untrusted_app 拥有 execute 权限），
 * 不依赖 sys_call_table，也不需要 SELinux permissive。
 *
 * 步骤①：改文件名（do_execveat_common）
 * 步骤②：换身份（begin_new_exec 改 bprm->cred + security_bprm_committing_creds 之后改 SID）
 */
extern bool __ksu_is_allow_uid_for_current(uid_t uid);

static char *ksu_su_path;
module_param_named(su_path, ksu_su_path, charp, 0644);
static int ksu_su_pid;
static unsigned long ksu_su_stamp;

static int ksu_su_exec_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct filename *fn = (struct filename *)regs->regs[1];
    const char *n;
    char *np;

    if (!ksu_su_path || !ksu_su_path[0])
        return 0;
    if (!fn || !fn->name)
        return 0;
    n = fn->name;
    if (strcmp(n, "/system/bin/su") && strcmp(n, "/system/xbin/su") && strcmp(n, "su"))
        return 0;
    pr_alert("ksu-dbg SU-EXEC uid=%d pid=%d n=%s\\n", current_uid().val, current->pid, n);
    if (current_uid().val != 2000 && !__ksu_is_allow_uid_for_current(current_uid().val))
        return 0;
    np = kstrdup(ksu_su_path, GFP_ATOMIC);
    if (!np)
        return 0;
    pr_alert("ksu: su-redirect uid=%d %s -> %s\\n", current_uid().val, n, ksu_su_path);
    ksu_su_pid = current->pid;
    ksu_su_stamp = jiffies;
    kfree(fn->name);
    fn->name = np;
    return 0;
}

static struct kprobe ksu_su_exec_kp = {
    .symbol_name = "do_execveat_common",
    .pre_handler = ksu_su_exec_pre,
};

/* 诊断：execve 系统调用入口（wrapper 一定存在，不受内联影响）。 */
static int ksu_exec_entry_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct pt_regs *real;
    static int _c;
    char buf[64];

    if (_c >= 8)
        return 0;
    real = (struct pt_regs *)regs->regs[0];
    if (!real)
        return 0;
    _c++;
    buf[0] = 0;
    if (copy_from_user_nofault(buf, (const void __user *)real->regs[0], sizeof(buf) - 1))
        return 0;
    buf[sizeof(buf) - 1] = 0;
    pr_alert("ksu-dbg ENTRY: uid=%d path=%s\\n", current_uid().val, buf);
    return 0;
}

static struct kprobe ksu_exec_entry_kp = {
    .symbol_name = "__arm64_sys_execve",
    .pre_handler = ksu_exec_entry_pre,
};

/* ── 步骤②：换身份 ────────────────────────────────────────────────
 * begin_new_exec() 里紧接着就是 security_bprm_committing_creds() 和
 * commit_creds(bprm->cred)，在这个 pre_handler 里改 bprm->cred 的字段，
 * 新程序就直接以 root 启动。全是已有结构体的字段赋值：不分配、不 sleep。
 */
static inline bool ksu_su_owned_by_current(void)
{
    return ksu_su_pid && ksu_su_pid == current->pid &&
           time_before(jiffies, ksu_su_stamp + 5 * HZ);
}

static int ksu_bprm_begin_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct linux_binprm *bprm = (struct linux_binprm *)regs->regs[0];
    struct cred *c;

    if (!ksu_su_owned_by_current())
        return 0;
    if (!bprm || !bprm->cred)
        return 0;
    c = bprm->cred;
    c->uid = c->euid = c->suid = c->fsuid = GLOBAL_ROOT_UID;
    c->gid = c->egid = c->sgid = c->fsgid = GLOBAL_ROOT_GID;
    c->securebits = 0;
    c->cap_inheritable = CAP_FULL_SET;
    c->cap_permitted = CAP_FULL_SET;
    c->cap_effective = CAP_FULL_SET;
    c->cap_bset = CAP_FULL_SET;
    c->cap_ambient = CAP_EMPTY_SET;
    ksu_su_pid = 0;
    pr_alert("ksu: su-cred -> root (pid=%d comm=%s)\\n", current->pid, current->comm);
    return 0;
}

static struct kprobe ksu_bprm_begin_kp = {
    .symbol_name = "begin_new_exec",
    .pre_handler = ksu_bprm_begin_pre,
};

/* SID 必须等 security_bprm_committing_creds() 算完域转换之后再改，否则会被策略结果覆盖。
 * cred->security 指向 task_security_struct，头两个 u32 是 osid/sid，把 sid 写成 kernel(1)。 */
static int ksu_sec_bprm_entry(struct kretprobe_instance *ri, struct pt_regs *regs)
{
    if (!ksu_su_owned_by_current())
        return 1;
    /* 返回时 regs->regs[0] 已被返回值占用，入口处先把 bprm 存进 ri->data */
    *(unsigned long *)ri->data = (unsigned long)regs->regs[0];
    return 0;
}

static int ksu_sec_bprm_ret(struct kretprobe_instance *ri, struct pt_regs *regs)
{
    struct linux_binprm *bprm = (struct linux_binprm *)*(unsigned long *)ri->data;
    struct cred *c;

    if (!bprm || !bprm->cred)
        return 0;
    c = bprm->cred;
    if (c->security) {
        *(u32 *)((char *)c->security + 4) = 1;
        pr_alert("ksu: su-sid -> kernel (pid=%d)\\n", current->pid);
    }
    return 0;
}

static struct kretprobe ksu_sec_bprm_krp = {
    .kp.symbol_name = "security_bprm_committing_creds",
    .entry_handler = ksu_sec_bprm_entry,
    .handler = ksu_sec_bprm_ret,
    .data_size = sizeof(unsigned long),
};

static void ksu_sp_hooks_init(void)
{
    int rc = register_kprobe(&ksu_sp_setresuid_kp);
    int rc2, rc3, rc4, rc5;

    pr_alert("ksu: setresuid kprobe rc=%d\\n", rc);
    rc2 = register_kprobe(&ksu_su_exec_kp);
    rc3 = register_kprobe(&ksu_exec_entry_kp);
    rc4 = register_kprobe(&ksu_bprm_begin_kp);
    rc5 = register_kretprobe(&ksu_sec_bprm_krp);
    pr_alert("ksu: su-exec kprobe rc=%d path=%s\\n", rc2, ksu_su_path ? ksu_su_path : "(null)");
    pr_alert("ksu: exec-entry rc=%d begin_new_exec rc=%d bprm-kretprobe rc=%d\\n", rc3, rc4, rc5);
}
'''
        s = s.rstrip() + "\n"
        # 我们的块必须落在 __aarch64__ 守卫内：把原来的 #endif 挪到文件尾
        if "#endif /* __aarch64__ */" in s:
            s = s.replace("#endif /* __aarch64__ */", "", 1)
            s = s.rstrip() + "\n#endif /* __aarch64__ */\n"
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
