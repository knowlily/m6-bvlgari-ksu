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
import sys

MARKER = "ksu_mgr_pkgs"          # 幂等标记

BLOCK = r'''
/* ── m6 补丁：多管理器包名 ──────────────────────────────────────────
 * loader 通过 mgr_pkgs=a,b,c 注入；留空则退回编译期 KSU_PACKAGE_NAME。
 * 这样一份 ko 能同时认官方 / Next / BakaSU 等多个管理器。
 */
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

REPLACES = [
    ("strncmp(np->package, KSU_PACKAGE_NAME, KSU_MAX_PACKAGE_NAME) == 0",
     "ksu_pkg_match(np->package)"),
    ("strncmp(pkg, KSU_PACKAGE_NAME, sizeof(KSU_PACKAGE_NAME)) == 0",
     "ksu_pkg_match(pkg)"),
    ("strcmp(np->package, KSU_PACKAGE_NAME) == 0",
     "ksu_pkg_match(np->package)"),
]


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

    # 2) 再插入 helper（放在第一个 crown_manager 定义之前；找不到就退回第一处 #define 之前）
    anchor = "static void crown_manager("
    if anchor in src:
        src = src.replace(anchor, BLOCK + anchor, 1)
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
