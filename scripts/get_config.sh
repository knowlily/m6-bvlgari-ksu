#!/system/bin/sh
# 在设备上取真实内核 config（root 或 shell 均可试）
# 用法：/dev/su debug su < scripts/get_config.sh   或   sh get_config.sh
OUT=/data/local/tmp/device.config

if [ -s /proc/config.gz ]; then
  echo "== /proc/config.gz 存在，直接解"
  zcat /proc/config.gz > "$OUT" && echo "OK -> $OUT ($(wc -l < $OUT) 行)"
  exit 0
fi

echo "== /proc/config.gz 不存在，尝试从 boot 分区提取 IKCONFIG"
for part in /dev/block/by-name/boot_a /dev/block/by-name/boot /dev/block/bootdevice/by-name/boot_a /dev/block/bootdevice/by-name/boot; do
  if [ -e "$part" ]; then
    echo "读 $part"
    dd if="$part" of=/data/local/tmp/boot.img bs=1M 2>/dev/null
    break
  fi
done

if [ -s /data/local/tmp/boot.img ]; then
  # 直接在设备上找 IKCFG_ST 魔数之后的 gzip 流（不依赖 extract-ikconfig）
  python3 - <<'PY' 2>/dev/null || true
data = open("/data/local/tmp/boot.img","rb").read()
i = data.find(b"IKCFG_ST")
j = data.find(b"IKCFG_ED")
if i >= 0 and j > i:
    blob = data[i+8:j]
    open("/data/local/tmp/device.config","wb").write(blob)
    print("提取到 IKCFG 段 %d 字节" % len(blob))
else:
    print("boot.img 里没有 IKCFG_ST（CONFIG_IKCONFIG 可能没开）")
PY
fi

if [ -s /data/local/tmp/device.config ]; then
  echo "OK -> /data/local/tmp/device.config"
  head -3 /data/local/tmp/device.config
else
  echo "!! 没拿到 config：CONFIG_IKCONFIG 未开，需要另一条路（厂商 defconfig 猜测）"
fi
