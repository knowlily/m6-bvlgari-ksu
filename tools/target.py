#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档位解析 / ko 校验 / CI 矩阵生成。

为什么要有这个脚本：原来「编哪个内核」这件事散在 workflow 的 6 个 input 里
（sublevel / localversion / source_url / config ...），加一个固件就要人肉记住一整套值，
错一个字符 vermagic 就对不上、内核直接拒载。现在只有一处真源：config/targets.json。

用法：
    python tools/target.py list
    python tools/target.py resolve --target os11                 # KEY=VALUE（给 GITHUB_ENV / shell eval）
    python tools/target.py resolve --target os11 --json
    python tools/target.py vermagic --target os11 --ko out/kernelsu_finasu121_os11.ko
    python tools/target.py matrix --targets m6,os11 --managers sukisu,finasu121,ksu331
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
TARGETS = os.path.join(ROOT, "config", "targets.json")
MANAGERS = os.path.join(ROOT, "config", "managers.json")


def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def get_target(name: str) -> dict:
    targets = load(TARGETS)
    if name not in targets:
        sys.exit(f"!! 未知档位 {name!r}，可选：{', '.join(targets)}")
    t = dict(targets[name])
    t["name"] = name
    # config 缺失时回退（os11 还没从设备抓过 config 的情况）
    cfg = os.path.join(ROOT, t["config"])
    if not os.path.exists(cfg) and t.get("config_fallback"):
        fb = os.path.join(ROOT, t["config_fallback"])
        if os.path.exists(fb):
            print(f"[target] {t['config']} 不存在，回退 {t['config_fallback']}", file=sys.stderr)
            t["config"] = t["config_fallback"]
    return t


def cmd_list(_args) -> int:
    for name, t in load(TARGETS).items():
        print(f"{name:6s} {t['kernel_release']:45s} {t['label']}")
    return 0


def cmd_resolve(args) -> int:
    t = get_target(args.target)
    # 产物后缀决定文件名：kernelsu_<alias><suffix>.ko
    rows = {
        "TARGET": t["name"],
        "KERNEL_BUILD": t["kernel_build"],
        "KERNEL_RELEASE": t["kernel_release"],
        "SUBLEVEL": args.sublevel or t["sublevel"],
        "LOCALVERSION": args.localversion or t["localversion"],
        "CONFIG_PATH": t["config"],
        "CRC_PATH": t["crc"],
        "SOURCE_VAR": t["source_var"],
        "ARTIFACT_SUFFIX": t["artifact_suffix"],
        "PAYLOAD_DIR": t["payload_dir"],
    }
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    # 默认写成 KEY=VALUE，方便 `>> $GITHUB_ENV` 或 `eval $(...)`
    for k, v in rows.items():
        print(f"{k}={v}")
    return 0


def ko_vermagic(path: str) -> str:
    """从 .modinfo 里抠 vermagic= 后面的第一个字段。"""
    with open(path, "rb") as f:
        blob = f.read()
    key = b"vermagic="
    i = blob.find(key)
    if i < 0:
        return ""
    tail = blob[i + len(key):i + len(key) + 200]
    out = []
    for ch in tail:
        if ch in (0, 10, 13):
            break
        out.append(chr(ch))
    return "".join(out).split(" ")[0]


def cmd_vermagic(args) -> int:
    t = get_target(args.target)
    if not os.path.exists(args.ko):
        print(f"!! 找不到 ko：{args.ko}")
        return 2
    got = ko_vermagic(args.ko)
    want = t["kernel_release"]
    size = os.path.getsize(args.ko)
    if got == want:
        print(f"OK  {os.path.basename(args.ko)}  {size}B  vermagic={got}")
        return 0
    print(f"FAIL {os.path.basename(args.ko)}  {size}B")
    print(f"     want {want}")
    print(f"     got  {got or '<读不到>'}")
    print(f"     ⇒ 这份 ko 不是 {t['label']} 档的，装载会被内核拒（vermagic 逐字比对）")
    return 1


def cmd_matrix(args) -> int:
    targets = [x.strip() for x in args.targets.split(",") if x.strip()]
    mgrs = load(MANAGERS)
    if args.managers in ("all", "*", ""):
        names = list(mgrs)
    else:
        names = [x.strip() for x in args.managers.split(",") if x.strip()]
    bad = [n for n in names if n not in mgrs]
    if bad:
        sys.exit(f"!! 未知管理器 {bad}，可选：{', '.join(mgrs)}")
    include = []
    for tn in targets:
        t = get_target(tn)
        for m in names:
            mg = mgrs[m]
            include.append({
                "target": tn,
                "localversion": t["localversion"],
                "sublevel": t["sublevel"],
                "config": t["config"],
                "alias": m,
                "ksu_repo": mg["repo"],
                "ksu_ref": mg["ref"],
                "ksu_tarball": f"https://github.com/{mg['repo']}/archive/refs/tags/{mg['ref']}.tar.gz"
                               if mg["tarball"] and not mg["ref"].startswith(("d5e9e025", "main"))
                               else "",
                "artifact": f"kernelsu_{m}{t['artifact_suffix']}.ko",
            })
    print(json.dumps({"include": include}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(fn=cmd_list)

    r = sub.add_parser("resolve")
    r.add_argument("--target", required=True)
    r.add_argument("--sublevel", default="", help="显式覆盖（默认取 targets.json）")
    r.add_argument("--localversion", default="", help="显式覆盖（默认取 targets.json）")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_resolve)

    v = sub.add_parser("vermagic")
    v.add_argument("--target", required=True)
    v.add_argument("--ko", required=True)
    v.set_defaults(fn=cmd_vermagic)

    m = sub.add_parser("matrix")
    m.add_argument("--targets", default="m6")
    m.add_argument("--managers", default="all")
    m.set_defaults(fn=cmd_matrix)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
