#!/usr/bin/env python3
"""拿设备 436 个厂商 ko 的 __versions 当基准，校验我们编的 ko 的符号 CRC 是否全对。

用法: python3 scripts/check_crc.py artifacts/kernelsu.ko config/device_crc.json
退出码: 0=全对  1=有对不上的符号
"""
import json, struct, sys

def sections(data):
    e_shoff, = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    shs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name, typ, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from("<IIQQQQIIQQ", data, off)
        shs.append(dict(name=name, offset=offset, size=size))
    st = shs[e_shstrndx]
    names = data[st["offset"]:st["offset"] + st["size"]]
    for s in shs:
        s["sname"] = names[s["name"]:names.find(b"\0", s["name"])].decode("latin1")
    return shs

def crc_pairs(path):
    data = open(path, "rb").read()
    out = {}
    for s in sections(data):
        if s["sname"] == "__versions" or s["sname"].startswith("__version_ext"):
            blob = data[s["offset"]:s["offset"] + s["size"]]
            for i in range(0, len(blob) - 63, 64):
                crc, = struct.unpack_from("<Q", blob, i)
                name = blob[i + 8:i + 64].split(b"\0")[0].decode("latin1")
                if name:
                    out[name] = crc
    return out

def main(ko, base):
    mine = crc_pairs(ko)
    dev = json.load(open(base))
    miss = {k: v for k, v in mine.items() if k not in dev}
    bad = {k: (v, dev[k]) for k, v in mine.items() if k in dev and dev[k] != v}
    print(f"ko 导入符号 {len(mine)} 个；基准表 {len(dev)} 个")
    print(f"  CRC 一致: {len(mine) - len(bad) - len(miss)}")
    print(f"  CRC 不一致: {len(bad)}")
    print(f"  基准表里没有(设备没模块用到): {len(miss)}")
    for k, (a, b) in list(bad.items())[:40]:
        print(f"   ! {k}: 我们=0x{a:08x} 设备=0x{b:08x}")
    for k in list(miss)[:10]:
        print(f"   ? {k} (无基准)")
    return 0 if not bad else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
