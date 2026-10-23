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
#include <linux/capability.h>
#include <linux/binfmts.h>\n#include <linux/limits.h>\n#include <linux/task_work.h>
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
/* 管理器 uid 由 loader 通过模块参数传进来。
 * 不要引用内核里的 ksu_manager_appid —— BakaSU 分支没有这个全局，引用它会变成
 * 未解析外部符号，ko_patch2 查清单查不到就整体 ABORT（真机踩过）。 */
static int ksu_su_mgr_appid = -1;
module_param_named(su_mgr_appid, ksu_su_mgr_appid, int, 0644);

static char *ksu_su_path;
module_param_named(su_path, ksu_su_path, charp, 0644);
static int ksu_su_pid;
/* 换域（SELinux SID）只能交给 KSU 自己的 escape_with_root_profile()：
 * 它内部 prepare_creds() 是 GFP_KERNEL，kprobe 的原子上下文里不能调。
 * 所以 kprobe 只排队一个 task_work，真正提权在 exit_to_user_mode 之前跑，那里可以睡眠。
 * 弱引用：某些分支可能没有这个函数，弱引用为 NULL 时只打日志、不做事。 */
extern int escape_with_root_profile(void);

struct ksu_su_tw {
    struct callback_head cb;
    int pid;
};

static void ksu_su_tw_fn(struct callback_head *head)
{
    struct ksu_su_tw *tw = container_of(head, struct ksu_su_tw, cb);

    if (tw->pid == current->pid) {
        int r = escape_with_root_profile();
        pr_alert("ksu: su-twerk escape pid=%d rc=%d\\n", tw->pid, r);
    }
    kfree(tw);
}\n/* 步骤②默认开，可 su_cred=0 关掉做 A/B。原来还有个 kretprobe 往 cred->security+4\n * 盲写 SID=kernel —— 那是对 SELinux 私有结构体的野写，已删除。 */\nstatic int ksu_su_cred = 1;\nmodule_param_named(su_cred, ksu_su_cred, int, 0644);

static int ksu_su_exec_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct filename *fn = (struct filename *)regs->regs[1];
    const char *n;

    if (!ksu_su_path || !ksu_su_path[0])
        return 0;
    if (!fn || !fn->name)
        return 0;
    n = fn->name;
    if (strcmp(n, "/system/bin/su") && strcmp(n, "/system/xbin/su") && strcmp(n, "su"))
        return 0;
    {
        uid_t _u = current_uid().val;
        /* 2026-10-22：管理器(如 me.weishu.kernelsu)不在 allowlist 里，但 KSU 语义上它
         * 本来就该有 root —— 不放行的话，管理器自己的 su 永远走不到重定向。
         * 注意这里只影响"文件名改写"；真正换 cred 的步骤②仍按 pid 认领，
         * 且 ① 之外还有 allowlist/manager 两道门，普通 App 不会因此拿到 root。 */
        bool _mgr = (_u == 0) || ((ksu_su_mgr_appid > 0) && (_u % 100000 == (uid_t)ksu_su_mgr_appid));
        pr_alert("ksu-dbg SU-EXEC uid=%d pid=%d mgr=%d n=%s\\n", _u, current->pid, (int)_mgr, n);
        if (_u == 2000)
            return 0;
        if (!_mgr && !__ksu_is_allow_uid_for_current(_u))
            return 0;
    }
    /* ★ 绝不能 kfree(fn->name)：短路径时 getname_flags() 把名字放进 struct filename
     * 内嵌的 iname[] 里，fn->name 指向结构体内部 —— kfree 它属于非法释放，
     * 会破坏 slab，过一阵才 panic（2026-10-22 真机就是这么崩的）。
     * 改成原地覆盖：内嵌缓冲约 PATH_MAX，长路径分支也是 kmalloc(PATH_MAX)，
     * 都放得下 App 里的 lib 路径；超长则直接放弃重定向，一个指针都不动。 */
    if (strlen(ksu_su_path) >= PATH_MAX)
        return 0;
    memcpy((char *)fn->name, ksu_su_path, strlen(ksu_su_path) + 1);
    ksu_su_pid = current->pid;
    pr_alert("ksu: su-redirect uid=%d %s -> %s\\n", current_uid().val, n, ksu_su_path);
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
/* 只比 pid：从 do_execveat_common 到 begin_new_exec 是同一次系统调用内部，
 * 窗口是微秒级，pid 不可能被复用；不用 jiffies 是为了不引入新的未定义符号。 */
static inline bool ksu_su_owned_by_current(void)
{
    return ksu_su_pid && ksu_su_pid == current->pid;
}

static int ksu_bprm_begin_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct linux_binprm *bprm = (struct linux_binprm *)regs->regs[0];
    struct cred *c;

    if (!ksu_su_cred)
        return 0;
    if (!ksu_su_owned_by_current())
        return 0;
    if (ksu_su_cred == 2) {
        struct ksu_su_tw *tw = kmalloc(sizeof(*tw), GFP_ATOMIC);
        if (!tw)
            return 0;
        tw->pid = current->pid;
        init_task_work(&tw->cb, ksu_su_tw_fn);
        if (task_work_add(current, &tw->cb, TWA_RESUME)) {
            kfree(tw);
            return 0;
        }
        pr_alert("ksu: su-cred -> 排队 task_work（改域交给 escape_with_root_profile）\\n");
        return 0;
    }
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

/* ==== access()/stat() 伪装 ====
 * shell 在 exec 前会先判断目标是否存在：access("/system/bin/su", X_OK)。
 * 真机上这个文件不存在 -> 检查失败 -> 直接 "inaccessible or not found"，
 * 走不到 execve，步骤①的钩子就永远不会触发（真机日志证实）。
 * 这里把 access/stat 的路径参数改写成我们那份 ksud 副本（apk_data_file，
 * 确实存在且可执行），检查通过；紧接着的 execve 再由步骤①换成同一文件。
 * 只对 allowlist / 管理器 uid 生效，别的进程连一次 strlen 都不做。 */
static int ksu_su_check_on = 1;
module_param_named(su_check, ksu_su_check_on, int, 0644);
/* 调试：对指定 uid 的每一次 access/stat 调用都打日志（含路径）。
 * 2026-10-22：真机上 su-check 一次都没命中，却不知道是「钩子没触发」还是
 * 「触发了但路径不同」——留这个旋钮直接看原始调用，别靠猜。 */
static int ksu_su_dbg_on = 0;
module_param_named(su_dbg, ksu_su_dbg_on, int, 0644);
static int ksu_su_dbg_uid = 0;
module_param_named(su_dbg_uid, ksu_su_dbg_uid, int, 0644);

/* ★ 2026-10-23 真机结论：kprobe 里 strncpy_from_user_nofault() 在本机一律失败
 * （返回 -EFAULT，日志里全是 <unreadable>），而 copy_from_user_nofault() 在同一
 * 上下文是好的（exec 钩子就是这么读文件名的，路径打得出来）。所以这里统一用后者
 * 手工截断。这一条解释了「钩子挂上 rc=0、路径也匹配，却一次都没命中」的全过程。 */
static int ksu_su_read_user(char *dst, int n, const void __user *src)
{
    if (!src || n < 2)
        return -1;
    /* ★ 2026-10-23：App 进程（MTE 开启）的用户指针最高字节带标签，
     * access_ok() 会把它判成越界直接 -EFAULT —— 真机表现就是同一个钩子
     * 对 adb shell(uid 2000) 能读出路径、对 App(uid 10244) 全是 <unreadable>。
     * 读之前统一砍掉高字节；没开 MTE 的指针高字节本来就是 0，无副作用。 */
    src = (const void __user *)((unsigned long)src & ~0xFF00000000000000UL);
    memset(dst, 0, n);
    if (copy_from_user_nofault(dst, src, n - 1))
        return -1;
    dst[n - 1] = 0;
    return (int)strlen(dst);
}

static int ksu_su_check_name(const char __user *ufn)
{
    char buf[32];

    if (!ufn)
        return 0;
    if (ksu_su_read_user(buf, sizeof(buf), (const void __user *)ufn) <= 0)
        return 0;
    return !strcmp(buf, "/system/bin/su") || !strcmp(buf, "/system/xbin/su") ||
           !strcmp(buf, "/sbin/su") || !strcmp(buf, "su");
}

/* 拷到用户栈指针下方，再把系统调用的路径参数指过去。
 * copy_to_user 出错（缺页/越界）就返回 NULL，调用方原样放行，绝不动寄存器。 */
static char __user *ksu_su_push_path(const char *s, unsigned long usp)
{
    size_t len = strlen(s) + 1;
    char __user *p;

    if (len > 512)
        return NULL;
    p = (char __user *)((usp - len) & ~0xFUL);
    if (copy_to_user_nofault(p, s, len)) {
        pr_alert("ksu: su-push fail len=%zu usp=%lx", len, usp);
        return NULL;
    }
    return p;
}

/* ★ 2026-10-23 真机结论（第二次）：arm64 上 SYSCALL_DEFINEx 生成的
 * __arm64_sys_* 只收一个参数 —— struct pt_regs *。所以在这些包装函数的入口，
 * x0 是 pt_regs 指针，真正的 syscall 参数在它里面；直接读 x1 拿到的是垃圾，
 * copy_from_user_nofault 必然 -EFAULT（日志里清一色 <unreadable>）。
 * do_faccessat / vfs_statx 是普通函数，参数就在 x1，不能一视同仁。 */
static struct pt_regs *ksu_su_real_regs(struct kprobe *p, struct pt_regs *regs)
{
    if (p->symbol_name && !strncmp(p->symbol_name, "__arm64_sys_", 12)) {
        struct pt_regs *rp = (struct pt_regs *)regs->regs[0];
        return rp ? rp : NULL;
    }
    return regs;
}

static int ksu_su_check_pre(struct kprobe *p, struct pt_regs *regs)
{
    uid_t u = current_uid().val;
    struct pt_regs *rp;
    u64 *argp;   /* arm64 的 pt_regs.regs 是 u64[]，用 unsigned long* 会类型不兼容 */
    unsigned long usp;
    bool mgr;

    if (!ksu_su_check_on || !ksu_su_path || !ksu_su_path[0])
        return 0;
    rp = ksu_su_real_regs(p, regs);
    if (!rp)
        return 0;
    argp = &rp->regs[1];
    usp = rp->sp;
    if (ksu_su_dbg_on && (int)u == ksu_su_dbg_uid) {
        char db[48];
        if (ksu_su_read_user(db, sizeof(db), (const void __user *)*argp) >= 0) {
            pr_alert("ksu: dbg-check uid=%d %s ptr=%lx %s\\n", u,
                     p->symbol_name ? p->symbol_name : "?", (unsigned long)*argp, db);
        } else {
            pr_alert("ksu: dbg-check uid=%d %s <unreadable>\\n", u,
                     p->symbol_name ? p->symbol_name : "?");
        }
    }
    if (u == 2000)
        return 0;
    /* uid 0 必须放行：aShell 的 root shell 早就是 root 了，从它里面敲 su
     * 就是「root 进程去 access(/system/bin/su)」—— 之前这里把它挡了，
     * 于是 0 次命中、shell 照旧报 inaccessible or not found。 */
    mgr = (u == 0) || ((ksu_su_mgr_appid > 0) && ((u % 100000) == (uid_t)ksu_su_mgr_appid));
    if (!mgr && !__ksu_is_allow_uid_for_current(u))
        return 0;
    /* faccessat(dfd,path,mode) / faccessat2(dfd,path,mode,flags)
     * newfstatat(dfd,path,buf,flags) / statx(dfd,path,flags,mask,buf) -- path 都在 x1 */
    if (!ksu_su_check_name((const char __user *)*argp))
        return 0;
    {
        char __user *np = ksu_su_push_path(ksu_su_path, usp);
        if (!np) {
            pr_alert("ksu: su-check push fail uid=%d pid=%d\\n", u, current->pid);
            return 0;
        }
        *argp = (unsigned long)np;
        pr_alert("ksu: su-check uid=%d pid=%d via=%s\\n", u, current->pid,
                 p->symbol_name ? p->symbol_name : "?");
    }
    return 0;
}

static struct kprobe ksu_su_check_kps[] = {
    {.symbol_name = "__arm64_sys_faccessat",  .pre_handler = ksu_su_check_pre},
    {.symbol_name = "__arm64_sys_faccessat2", .pre_handler = ksu_su_check_pre},
    {.symbol_name = "__arm64_sys_newfstatat", .pre_handler = ksu_su_check_pre},
    {.symbol_name = "__arm64_sys_statx",      .pre_handler = ksu_su_check_pre},
    /* 兜底：万一 bionic 的入口不叫 __arm64_sys_*，这两个是 VFS 层必经点 */
    {.symbol_name = "do_faccessat",           .pre_handler = ksu_su_check_pre},
    {.symbol_name = "vfs_statx",              .pre_handler = ksu_su_check_pre},
};

static void ksu_sp_hooks_init(void)
{
    int rc = register_kprobe(&ksu_sp_setresuid_kp);
    int rc2, rc3, rc4;

    pr_alert("ksu: setresuid kprobe rc=%d\\n", rc);
    rc2 = register_kprobe(&ksu_su_exec_kp);
    rc3 = register_kprobe(&ksu_exec_entry_kp);
    rc4 = register_kprobe(&ksu_bprm_begin_kp);
    pr_alert("ksu: su-exec kprobe rc=%d path=%s\\n", rc2, ksu_su_path ? ksu_su_path : "(null)");
    pr_alert("ksu: exec-entry rc=%d begin_new_exec rc=%d su_cred=%d\\n", rc3, rc4, ksu_su_cred);
    {
        int _i, _rc;
        for (_i = 0; _i < (int)(sizeof(ksu_su_check_kps) / sizeof(ksu_su_check_kps[0])); _i++) {
            _rc = register_kprobe(&ksu_su_check_kps[_i]);
            pr_alert("ksu: su-check kprobe[%d] %s rc=%d\\n", _i,
                     ksu_su_check_kps[_i].symbol_name, _rc);
        }
    }
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
