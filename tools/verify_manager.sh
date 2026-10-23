#!/bin/bash
# 管理器接管 —— 验证矩阵（只读，不改设备）
# 用法：bash tools/verify_manager.sh
set -u
A="adb"
say(){ printf '\n\033[1m%s\033[0m\n' "$*"; }
run(){ $A shell "$1" 2>/dev/null | tr -d '\r'; }

say "1) 设备 / 内核 / ko"
run 'getprop ro.product.model; getprop ro.build.display.id'
echo "内核      : $(run 'uname -r')"
echo "vermagic  : $(run 'grep -a -m1 -o "vermagic=[^ ]*" /data/local/tmp/route/m6/kernelsu_next.ko 2>/dev/null')"
echo "模块      : $(run 'grep -a kernelsu /proc/modules')"

say "2) 已装的管理器"
for p in me.weishu.kernelsu kqvdie.zeykvw.hpbcyd com.bakasu.bakasu com.resukisu.resukisu com.resukisu.ksu; do
  if run "pm path $p" | grep -q package; then
    printf "  %-26s 已装  uid=%s  ver=%s\n" "$p" \
      "$(run "stat -c %u /data/data/$p")" \
      "$(run "dumpsys package $p | grep -m1 versionName | tr -d ' '")"
  fi
done

say "3) ko 选择依据"
echo "MANAGER_PKG marker : $(run 'cat /data/local/tmp/route/m6/MANAGER_PKG 2>/dev/null')"
echo "App 私有文件       : $(run 'cat /data/data/com.dsh.ppgroot/files/manager_pkg 2>/dev/null')"
echo "loader 实际选的 ko : $(run 'grep -a "自编ko" /data/local/tmp/route/m6/ksu_load.log 2>/dev/null | tail -1')"

say "4) 内核参数"
for p in su_cred su_check su_dbg su_dbg_uid su_mgr_appid; do
  v=$(run "cat /sys/module/kernelsu/parameters/$p 2>/dev/null")
  printf "  %-12s %s\n" "$p" "${v:-（无此参数）}"
done

say "5) 授权链路（allowlist）"
echo "ioctl 次数            : $(run 'grep -ac "ksu ioctl" /data/local/tmp/route/m6/kmsg_cap.txt 2>/dev/null')"
echo "最近一次 ioctl 命令   : $(run 'grep -a "ksu ioctl" /data/local/tmp/route/m6/kmsg_cap.txt 2>/dev/null | tail -1 | sed "s/.*KernelSU: //"')"

say "6) 模块生态（需要开机就有 root 才有意义）"
run 'ls -l /data/adb/modules 2>/dev/null | head -6'
echo "ksud      : $(run 'ls -l /data/adb/ksud 2>/dev/null | awk "{print \$5\" 字节\"}"')"
echo "ksu 目录  : $(run 'ls /data/adb/ksu 2>/dev/null | tr "\n" " "')"

say "7) per-App profile 是否生效（要人操作）"
cat <<'EOF'
  在管理器 UI 里给某个 App 设一个显眼的 profile（例如 umask=077 或换 SELinux 域），
  然后在该 App 的终端里执行：

      su -c 'id; umask; cat /proc/self/attr/current'

  若 umask/域跟着 profile 变 ⇒ 接管已生效（escape_with_root_profile 内部按 uid 取 profile）。
  若仍是默认值 ⇒ 管理器的 SET_APP_PROFILE 没写进内核 allow_list，需要查 supercall。
EOF
