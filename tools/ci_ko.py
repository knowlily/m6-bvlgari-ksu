#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 产物 → payload 的机械搬运（去掉「下载 / 改名 / 复制 / 手抄 md5」四步人肉活）。

原来这条链每次都要人做：
    Actions 页面点下载 → 解压 → 改成 kernelsu_<alias>.ko → 拖进 payload/
    → 跑 rebuild_manifest.py --sync → 重编 APK
其中「先编译后 sync」踩过一次（包里还是旧 payload）。现在：

    python tools/ci_ko.py pull --target os11            # 拉最新一次成功产物，自动改名 + 校验 vermagic
    python tools/ci_ko.py pull --target os11 --run 40281648326 --alias finasu121
    python tools/ci_ko.py pull --target os11 --sync     # 顺便同步进 App assets（= rebuild_manifest 的活儿）

判据（不符就拒收，不往 payload 里放）：
  · 文件名必须是 kernelsu_<alias><suffix>.ko
  · vermagic 必须逐字等于 targets.json 里该档位的 kernel_release
  · 大小 > 16 KB 且前 4 字节是 ELF magic
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from target import get_target, ko_vermagic  # noqa: E402

REPO = "knowlily/m6-bvlgari-ksu"
WORKFLOW = "build-ksu-ko.yml"
JAILBREAK = os.path.abspath(os.path.join(ROOT, "..", "m6-jailbreak-core"))


def sh(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"!! 命令失败: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def latest_runs(limit=40):
    out = sh(["gh", "run", "list", "--repo", REPO, "--workflow", WORKFLOW,
              "--limit", str(limit), "--json", "databaseId,conclusion,createdAt,displayTitle"])
    return json.loads(out or "[]")


def artifacts_of(run_id):
    out = sh(["gh", "api", f"repos/{REPO}/actions/runs/{run_id}/artifacts",
              "--jq", '[.artifacts[] | select(.expired==false) | {name: .name, id: .id}]'])
    return json.loads(out or "[]")


def pick_target_artifacts(run_id, suffix):
    """只认 ksu-ko-* 且带档位后缀的 artifact（buildlog 不要）。"""
    out = []
    for a in artifacts_of(run_id):
        n = a["name"]
        if not n.startswith("ksu-ko-"):
            continue
        # os11 的 artifact 名形如 ksu-ko-sukisu_os11-v4.2.1-r1
        if suffix and suffix not in n:
            continue
        if not suffix and "_os11" in n:
            continue
        out.append(a)
    return out


def validate(path, target):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        magic = f.read(4)
    if size < 16384:
        return f"只有 {size}B，像占位空文件"
    if magic != b"\x7fELF":
        return f"不是 ELF（前 4 字节 {magic.hex()}）"
    got = ko_vermagic(path)
    if got != target["kernel_release"]:
        return f"vermagic 不符：got {got or '<空>'} / want {target['kernel_release']}"
    return ""


def cmd_pull(args) -> int:
    t = get_target(args.target)
    suffix = t["artifact_suffix"]
    # payload 在**兄弟仓库** m6-jailbreak-core 里（targets.json 的 payload_dir 相对它）
    dest = os.path.abspath(os.path.join(JAILBREAK, t["payload_dir"]))
    os.makedirs(dest, exist_ok=True)

    run_id = args.run
    artifacts = []
    if run_id in ("", "latest", None):
        for r in latest_runs():
            if r["conclusion"] != "success":
                continue
            arts = pick_target_artifacts(r["databaseId"], suffix)
            if arts:
                run_id, artifacts = r["databaseId"], arts
                print(f"[ci] 取最近一次成功 run {run_id}（{r['createdAt']}）")
                break
        if not artifacts:
            print(f"!! 最近 40 次里没有 target={args.target} 的成功产物。")
            print(f"   先跑：gh workflow run {WORKFLOW} -f target={args.target} -f ksu_repo=... -f ksu_ref=...")
            return 1
    else:
        artifacts = pick_target_artifacts(run_id, suffix)
        if not artifacts:
            print(f"!! run {run_id} 里没有 target={args.target} 的 artifact")
            return 1

    ok, bad = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for a in artifacts:
            sub = os.path.join(tmp, a["name"])
            os.makedirs(sub, exist_ok=True)
            sh(["gh", "run", "download", str(run_id), "--repo", REPO, "-n", a["name"], "-D", sub])
            kos = [os.path.join(sub, f) for f in os.listdir(sub) if f.endswith(".ko")]
            if not kos:
                bad.append((a["name"], "产物里没有 .ko"))
                continue
            for k in kos:
                err = validate(k, t)
                if err:
                    bad.append((os.path.basename(k), err))
                    continue
                # 强制改成 <alias><suffix>.ko：loader 和 bundled_ko.md5 都按这个名字找
                base = os.path.basename(k)
                alias = base[len("kernelsu_"):-len(".ko")]
                if suffix and not alias.endswith(suffix):
                    alias += suffix
                final = f"kernelsu_{alias}.ko"
                if args.alias and args.alias not in alias:
                    continue
                if args.dry_run:
                    print(f"  [dry] {base} → {t['payload_dir']}/{final}")
                else:
                    shutil.copy2(k, os.path.join(dest, final))
                    print(f"  ✓ {base} → {os.path.join(t['payload_dir'], final)}  "
                          f"({os.path.getsize(k)}B, {ko_vermagic(k)})")
                ok.append(final)

    for name, err in bad:
        print(f"  ✗ {name}: {err}")

    if args.sync and ok and not args.dry_run:
        py = sys.executable
        print(sh([py, os.path.join(JAILBREAK, "tools", "rebuild_manifest.py"), "--sync"],
                 check=False).strip())
    print(f"[done] 收下 {len(ok)} 份，拒收 {len(bad)} 份；目录 {dest}")
    return 0 if ok else 1


def cmd_status(args) -> int:
    t = get_target(args.target)
    dest = os.path.abspath(os.path.join(JAILBREAK, t["payload_dir"]))
    print(f"target={args.target}  {t['kernel_release']}")
    print(f"目录  {dest}")
    if not os.path.isdir(dest):
        print("  （目录还不存在）")
        return 1
    rows = [f for f in sorted(os.listdir(dest)) if f.endswith(".ko") and f.startswith("kernelsu")]
    if not rows:
        print("  （还没有 ko）")
        return 1
    for f in rows:
        p = os.path.join(dest, f)
        got = ko_vermagic(p)
        mark = "OK  " if got == t["kernel_release"] else "BAD "
        print(f"  {mark} {f:34s} {os.path.getsize(p):>9}B  {got or '<无 vermagic>'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pull", help="从 Actions 拉产物并按档位落进 payload")
    p.add_argument("--target", required=True)
    p.add_argument("--run", default="latest")
    p.add_argument("--alias", default="", help="只要这个 alias（如 finasu121）")
    p.add_argument("--sync", action="store_true", help="落盘后跑 rebuild_manifest.py --sync")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_pull)

    s = sub.add_parser("status", help="看看 payload 里的 ko 与档位对不对得上")
    s.add_argument("--target", required=True)
    s.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
