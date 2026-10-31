#!/bin/sh
# Choose the system for the next start, then restart through NextUI's normal
# path (the launcher reboots when /tmp/reboot exists after a tool exits).
#   stock: BaseOS 1.5.3 (vendor kernel)   alpha: BaseOS 2 (mainline)
# The boot loader starts BaseOS 2 when the RTC pair at 0x07000138/0x0700013c
# is zero and BaseOS 1.5.3 otherwise; BaseOS 2 clears it after each good start.
. /mnt/sdcard/.system/h700/bin/alpha-runtime.sh
alpha_select_abi
case "$1:$BASEOS_MAINLINE" in
stock:0|alpha:1) exit 0 ;;
stock:1) /usr/sbin/mem 0x07000138 0x9f559c26 >/dev/null &&
	 /usr/sbin/mem 0x0700013c 0x42535031 >/dev/null || exit 1 ;;
alpha:0) /usr/sbin/native-health --hold-stock >/dev/null &&
	 /usr/sbin/native-health --release-stock >/dev/null || exit 1 ;;
*) exit 2 ;;
esac
touch /tmp/reboot
