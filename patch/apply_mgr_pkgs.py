#!/usr/bin/env python3
"""给 KernelSU 模块打「多管理器包名」补丁。

背景：KSU 原生的 track_throne() 用编译期常量 KSU_PACKAGE_NAME 认管理器，
一份 ko 只认一个包名。我们的设备上同时可能装官方 / Next / BakaSU 三种管理器，
各编一份 ko 太重，所以改成运行时参数：

  mgr_pkgs=a,b,c         —— 逗号分隔的包名白名单（空则退回 KSU_PACKAGE_NAME）
  mgr_dont_invalidate=1  —— 默认不执行 ksu_invalidate_manager_uid()，
                            防止 track_throne 把用户态 loader 加冕的 manager_appid 清掉

用法: python3 apply_mgr_pkgs.py <KSU 源码根目录>
幂等：重复执行不会叠加。
"""
import glob
import os
import re
import sys

MARKER = "ksu_mgr_pkgs"          # 幂等标记

BLOCK = r'''
/* ── m6 补丁：多管理器包名 ──────────────────────────────────────────
 * loader 通过 mgr_pkgs=a,b,c 注入；留空则退回编译期 KSU_PACKAGE_NAME。
 * 这样一份 ko 能同时认官方 / Next / BakaSU 等多个管理器。
 *
 * 注意：Next / BakaSU 的 throne_tracker.c 里没有 KSU_PACKAGE_NAME 这个宏
 * （它们的管理器识别走别的路径），而我们下面要引用它，所以这里必须有兜底定义，
 * 否则那两个分支会直接编译失败：use of undeclared identifier 'KSU_PACKAGE_NAME'。
 */
#ifndef KSU_PACKAGE_NAME
#define KSU_PACKAGE_NAME "me.weishu.kernelsu"
#endif
static char *ksu_mgr_pkgs;
module_param_named(mgr_pkgs, ksu_mgr_pkgs, charp, 0644);
static bool ksu_mgr_dont_invalidate = true;
module_param_named(mgr_dont_invalidate, ksu_mgr_dont_invalidate, bool, 0644);

static bool ksu_pkg_match(const char *pkg)
{
    const char *p = ksu_mgr_pkgs;
    size_t len;

    if (!pkg || !*pkg)
        return false;
    if (!p || !*p) {
        /* 没传参数：保持原生行为 */
        return strncmp(pkg, KSU_PACKAGE_NAME, sizeof(KSU_PACKAGE_NAME)) == 0;
    }
    while (p && *p) {
        const char *comma = strchr(p, ',');
        len = comma ? (size_t)(comma - p) : strlen(p);
        if (len && strlen(pkg) == len && !strncmp(pkg, p, len))
            return true;
        if (!comma)
            break;
        p = comma + 1;
    }
    return false;
}

'''

CROWN = r'''
static void crown_manager(const char *apk, struct list_head *uid_data)
{
    struct list_head *list = (struct list_head *)uid_data;
    struct uid_data *np;
    const char *p = ksu_mgr_pkgs;

    if (!p || !*p) {
        /* 没传 mgr_pkgs：完全保持原生行为（单包名） */
        list_for_each_entry (np, list, list) {
            if (strncmp(np->package, KSU_PACKAGE_NAME, KSU_MAX_PACKAGE_NAME) == 0) {
                pr_info("Crowning manager: uid=%d\n", np->uid);
                ksu_set_manager_appid(np->uid);
                break;
            }
        }
        return;
    }

    /*
     * 按 mgr_pkgs 的书写顺序优先，而不是按 packages.list 的遍历顺序。
     * 设备上同时装着官方 / ReSukiSU / Next 时，原先"任意匹配"会挑到字母序最靠前的
     * com.resukisu.resukisu（真机现象：Crowning manager: uid=10512，而用户装的
     * me.weishu.kernelsu 是 11207 ⇒ 管理器所有 ioctl 被 permission denied）。
     * loader 已经把"用户选中的那个管理器"写在第一位，这里就尊重这个顺序。
     */
    while (p && *p) {
        const char *comma = strchr(p, ',');
        size_t len = comma ? (size_t)(comma - p) : strlen(p);

        if (len) {
            list_for_each_entry (np, list, list) {
                if (strlen(np->package) == len && !strncmp(np->package, p, len)) {
                    pr_info("Crowning manager: %.*s uid=%d\n", (int)len, p, np->uid);
                    ksu_set_manager_appid(np->uid);
                    return;
                }
            }
        }
        if (!comma)
            break;
        p = comma + 1;
    }
    pr_info("Crowning manager: none of mgr_pkgs found on device\n");
}
'''

REPLACES = [
    ("strncmp(np->package, KSU_PACKAGE_NAME, KSU_MAX_PACKAGE_NAME) == 0",
     "ksu_pkg_match(np->package)"),
    ("strncmp(pkg, KSU_PACKAGE_NAME, sizeof(KSU_PACKAGE_NAME)) == 0",
     "ksu_pkg_match(pkg)"),
    ("strcmp(np->package, KSU_PACKAGE_NAME) == 0",
     "ksu_pkg_match(np->package)"),
]


def _replace_func(src, sig, new_text):
    """把以 sig 开头的函数整体替换成 new_text（大括号配平）。"""
    i = src.find(sig)
    if i < 0:
        return src, False
    j = src.find("{", i)
    if j < 0:
        return src, False
    depth = 0
    k = j
    while k < len(src):
        c = src[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return src[:i] + new_text.strip("\n") + src[k + 1:], True


def patch_file(path):
    src = open(path, encoding="utf-8", errors="surrogateescape").read()
    if MARKER in src:
        print(f"[mgr-pkgs] {os.path.relpath(path)}: 已打过，跳过")
        return False

    # 1) 先替换原生比较点（必须在插入 helper 之前，否则 helper 里那句
    #    strncmp(pkg, KSU_PACKAGE_NAME, ...) 会被自己替换成递归调用）
    hits = 0
    for old, new in REPLACES:
        n = src.count(old)
        if n:
            src = src.replace(old, new)
            hits += n

    # 2) 插入 helper + 顺序敏感地整只替换 crown_manager()
    anchor = "static void crown_manager("
    if anchor in src:
        src = src.replace(anchor, BLOCK + anchor, 1)
        # ★ 只对"2 参数"的 crown_manager 做整只替换。BakaSU 的是
        #   crown_manager(const char *apk, struct list_head *uid_data, u8 signature_index)
        #   且完全不用 KSU_PACKAGE_NAME（它按 apk 路径推包名 + 签名索引加冕），
        #   硬替换会变成 "too many arguments to function call, expected 2, have 3"。
        m = re.search(r"static void crown_manager\(([^)]*)\)", src)
        nparams = 0 if not m else len([x for x in m.group(1).split(",") if x.strip()])
        if nparams == 2:
            src, ok = _replace_func(src, "static void crown_manager(const char *apk", CROWN)
            print(f"[mgr-pkgs] crown_manager 顺序敏感替换: {'OK' if ok else '未命中'}")
            if not ok:
                print(f"[mgr-pkgs] {path}: 没找到 crown_manager 定义，加冕顺序未修")
        else:
            print(f"[mgr-pkgs] crown_manager 有 {nparams} 个参数，跳过顺序敏感替换（该分支按 apk 签名认管理器）")
    else:
        idx = src.find("#define")
        if idx < 0:
            print(f"[mgr-pkgs] {path}: 找不到插入点，跳过")
            return False
        src = src[:idx] + BLOCK + src[idx:]

    # 3) invalidate 加开关
    inv = "ksu_invalidate_manager_uid();"
    if inv in src:
        src = src.replace(inv, "if (!ksu_mgr_dont_invalidate)\n            " + inv)

    # 4) 需要 moduleparam（有的分支没包含）
    if "#include <linux/moduleparam.h>" not in src:
        src = src.replace("#include <linux/kernel.h>",
                          "#include <linux/kernel.h>\n#include <linux/moduleparam.h>", 1)

    open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n").write(src)
    print(f"[mgr-pkgs] {os.path.relpath(path)}: 替换 {hits} 处比较 + invalidate 开关")
    return True


def main():
    if len(sys.argv) < 2:
        print("用法: apply_mgr_pkgs.py <KSU 源码根目录>")
        return 1
    root = sys.argv[1]
    targets = glob.glob(os.path.join(root, "kernel", "**", "*throne*racker*.c"), recursive=True)
    if not targets:
        targets = glob.glob(os.path.join(root, "**", "*throne*.c"), recursive=True)
    if not targets:
        print("[mgr-pkgs] 没找到 throne_tracker.c，跳过（该分支可能没有这个文件）")
        return 0
    for t in targets:
        patch_file(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
