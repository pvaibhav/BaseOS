#!/bin/sh
# Post-flash functional validation for the base OS, run from the Mac against
# a booted device (enable WiFi in NextUI settings first, or use serial).
#
# Usage: ./validate-on-device.sh <target> <device-ip> [root-password]
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:?usage: validate-on-device.sh <target> <device-ip> [password]}"
IP="${2:?usage: validate-on-device.sh <target> <device-ip> [password]}"
PW="${3:-root}"
eval "$(python3 "$HERE/tools/device_profile.py" shell "$TARGET")"

sshpass -p "$PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 "root@$IP" \
	env BASEOS_EXPECTED_TARGET="$TARGET" BASEOS_EXPECTED_WIFI="$PROFILE_WIFI" \
	    BASEOS_EXPECTED_ROTATION="$PROFILE_PANEL_ROTATION_CCW" \
	    BASEOS_EXPECTED_PANEL_WIDTH="$PROFILE_BOOTLOGO_WIDTH" \
	    BASEOS_EXPECTED_PANEL_HEIGHT="$PROFILE_BOOTLOGO_HEIGHT" sh -s <<'REMOTE'
pass=0; fail=0
ok()   { echo "PASS: $1"; pass=$((pass+1)); }
bad()  { echo "FAIL: $1"; fail=$((fail+1)); }
chk()  { desc="$1"; shift; if eval "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi; }

echo "=== identity ==="
chk "running the base OS (/etc/baseos-release)" "grep -q BASEOS=1 /etc/baseos-release"
chk "exact BaseOS target ($BASEOS_EXPECTED_TARGET)" "grep -qx BASEOS_TARGET=$BASEOS_EXPECTED_TARGET /etc/baseos-release"
chk "no systemd running"                        "! pidof systemd"
chk "busybox is init (PID 1)"                   "grep -q busybox /proc/1/comm || readlink /proc/1/exe | grep -q busybox"
chk "release version recorded"                  "grep -q '^BASEOS_VERSION=' /etc/baseos-release"
chk "os-release agrees with baseos-release"     "grep -qx \"VERSION_ID=\$(sed -n 's/^BASEOS_VERSION=//p' /etc/baseos-release)\" /etc/os-release"

echo "=== partition layout & update slots ==="
chk "seven partitions, not eight"       "test \"\$(grep -c 'mmcblk0p' /proc/partitions)\" -eq 7"
chk "U-Boot saw the BaseOS names"       "grep -q 'rootfs@mmcblk0p5:UDISK@mmcblk0p6:primary@mmcblk0p7' /proc/cmdline"
chk "/data is the UDISK partition (p6)" "grep -q '^/dev/mmcblk0p6 /data ' /proc/mounts"
chk "gptslot accepts this layout"       "/usr/sbin/gptslot /dev/mmcblk0 geometry"
chk "update engine present"             "test -x /usr/sbin/baseos-update"
/usr/sbin/gptslot /dev/mmcblk0 geometry 2>/dev/null | sed 's/^/  /'
/usr/sbin/baseos-update status 2>/dev/null | sed 's/^/  /'

echo "=== boot speed ==="
if [ -f /run/boot-frontend-exec ]; then
	echo "  boot-frontend-exec: $(cat /run/boot-frontend-exec)s"
fi
chk "frontend exec marker exists" "test -f /run/boot-frontend-exec"
chk "regular boot stays within the 2.50s frontend-exec budget" \
	"awk 'NR == 1 { exit !(\$1 <= 2.50) }' /run/boot-frontend-exec"

echo "=== hardware ==="
chk "GPU module loaded (mali_kbase)"   "grep -q mali_kbase /proc/modules"
chk "GPU device node (/dev/mali0)"     "test -c /dev/mali0"
chk "display (/dev/disp + fb0)"        "test -c /dev/disp && test -c /dev/fb0"
# The splash and the bootlogo are both generated from the profile's panel
# dimensions, so a device that reports something else would render pre-turned
# artwork for a geometry it does not have. Only the width is compared: yres_virtual
# is several buffers deep for scanout, so the height is checked as a multiple.
chk "panel width matches the profile ($BASEOS_EXPECTED_PANEL_WIDTH)" \
	"test \"\$(cut -d, -f1 /sys/class/graphics/fb0/virtual_size)\" -eq $BASEOS_EXPECTED_PANEL_WIDTH"
chk "panel height is a whole number of $BASEOS_EXPECTED_PANEL_HEIGHT-row buffers" \
	"test \"\$(( \$(cut -d, -f2 /sys/class/graphics/fb0/virtual_size) % $BASEOS_EXPECTED_PANEL_HEIGHT ))\" -eq 0"
chk "splash knows the panel rotation ($BASEOS_EXPECTED_ROTATION ccw)" \
	"grep -qx BASEOS_PANEL_ROTATION_CCW=$BASEOS_EXPECTED_ROTATION /etc/baseos-release"
chk "HDMI hotplug state readable"      "test -r /sys/class/extcon/hdmi/state"
chk "display output switch (dispdbg)"  "test -w /sys/kernel/debug/dispdbg/command"
chk "input devices (event0-2)"         "test -c /dev/input/event0 && test -c /dev/input/event1"
chk "audio card 0 (audiocodec)"        "grep -q audiocodec /proc/asound/cards"
chk "battery sysfs (AXP2202)"          "test -r /sys/class/power_supply/axp2202-battery/capacity"
chk "thermal zones"                    "test -r /sys/class/thermal/thermal_zone0/temp"
chk "deep sleep available (mem)"       "grep -q mem /sys/power/state"
chk "rumble sysfs (moto)"              "test -w /sys/class/power_supply/axp2202-battery/moto"
if [ "$BASEOS_EXPECTED_WIFI" = 1 ]; then
	chk "wifi module loaded (8821cs)"  "grep -q 8821cs /proc/modules"
	chk "wifi interface (wlan0)"       "test -d /sys/class/net/wlan0"
else
	chk "wifi legitimately absent"     "! grep -q 8821cs /proc/modules && ! test -d /sys/class/net/wlan0"
fi

echo "=== NextUI ==="
chk "SD card mounted (/mnt/sdcard)"    "mountpoint -q /mnt/sdcard"
chk "nextui.elf running"               "pidof nextui.elf"
chk "keymon running"                   "pidof keymon.elf"

echo "=== dev services ==="
chk "sftp-server present + executable"  "test -x /usr/libexec/sftp-server"
chk "USB recovery listener running"      "pidof usb-gadget-watch"
chk "adbd running"                      "pidof adbd"
chk "adb has no TCP 5555 listener"       "! netstat -lnt 2>/dev/null | grep -q ':5555 '"
# The adb gadget is bound to the always-present UDC; device (peripheral) mode is
# auto-selected by the sunxi manager on cable attach. We deliberately do NOT
# force usbc0/otg_role — writing it wedges the writer in D-state on this vendor
# kernel, and its siblings usb_device/usb_host/usb_null are read-triggers that
# switch the role on cat. So the check is "gadget bound", not "role == device".
chk "adb gadget bound (g1/UDC)"         "test -n \"\$(cat /sys/kernel/config/usb_gadget/g1/UDC)\""
chk "functionfs mounted"                "grep -q functionfs /proc/mounts"

echo "=== resources ==="
echo "  processes: $(ps | wc -l)"
free | awk "/Mem:/ {printf \"  RAM used: %d/%d MB\n\", (\$2-\$7)/1024, \$2/1024}" 2>/dev/null || free
echo "  rootfs: $(df -h / | tail -1 | awk "{print \$3\" used, ro=\"}")$(grep " / " /proc/mounts | grep -o "[[:space:]]ro[,[:space:]]" | head -1)"
for z in 0 1 3; do
	t=$(cat /sys/class/thermal/thermal_zone$z/temp 2>/dev/null)
	[ -n "$t" ] && echo "  thermal_zone$z: $((t / 1000))°C"
done

echo
echo "=== RESULT: $pass passed, $fail failed ==="
exit $fail
REMOTE
