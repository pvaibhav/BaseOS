#!/bin/sh
# Prepare and build one or more BaseOS targets from a directory of StockMod
# .img files. With no explicit targets, all supported targets are required.
# Usage: ./build-stockmod.sh <firmware-directory> [target ...]
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
FIRMWARE_DIR="${1:?usage: $0 <firmware-directory> [target ...]}"
shift
[ -d "$FIRMWARE_DIR" ] || { echo "firmware directory not found: $FIRMWARE_DIR" >&2; exit 1; }
FIRMWARE_DIR="$(cd "$FIRMWARE_DIR" && pwd)"

if [ "$#" -eq 0 ]; then
	TARGETS="$(python3 "$HERE/tools/device_profile.py" list)"
else
	TARGETS="$*"
fi

# Validate target IDs, duplicates, and all filename matches before preparing
# anything. The second lookup below is therefore guaranteed unambiguous.
SEEN=" "
for target in $TARGETS; do
	python3 "$HERE/tools/device_profile.py" shell "$target" >/dev/null
	case "$SEEN" in *" $target "*) echo "duplicate target: $target" >&2; exit 2 ;; esac
	SEEN="$SEEN$target "
	python3 "$HERE/tools/device_profile.py" find "$FIRMWARE_DIR" "$target" >/dev/null
done

for target in $TARGETS; do
	image="$(python3 "$HERE/tools/device_profile.py" find "$FIRMWARE_DIR" "$target")"
	"$HERE/prepare-stock.sh" "$target" "$image"
done

# Rebuild when a binary is missing *or* when src/ has moved on since the cached
# ones were compiled. Existence alone is not enough: editing src/fbsplash.c and
# rebuilding used to ship the previous binary with the new overlay scripts.
if [ ! -x "$HERE/work/tools/busybox" ] \
  || [ ! -x "$HERE/work/tools/dropbearmulti" ] \
  || [ ! -x "$HERE/work/tools/curl" ] \
  || [ ! -x "$HERE/work/tools/fbsplash" ] \
  || [ ! -x "$HERE/work/tools/gptgrow" ] \
  || [ ! -x "$HERE/work/tools/gptslot" ] \
  || [ ! -x "$HERE/work/tools/sftp-server" ] \
  || [ ! -x "$HERE/work/tools/adbd" ] \
  || [ ! -x "$HERE/work/tools/avahi-daemon" ] \
  || [ ! -x "$HERE/work/tools/boot-clock" ] \
  || [ ! -x "$HERE/work/tools/baseos-regs" ] \
  || [ ! -x "$HERE/work/tools/e2fsck" ] \
  || ! "$HERE/tools/tools-stamp.sh" | cmp -s - "$HERE/work/tools/.stamp"; then
	"$HERE/build-tools.sh"
else
	echo "Reusing shared tools in $HERE/work/tools"
fi

for target in $TARGETS; do
	"$HERE/build-rootfs.sh" "$target"
	"$HERE/test-boot-qemu.sh" "$target"
	"$HERE/build-image.sh" "$target"
done

echo
echo "Built BaseOS images:"
for target in $TARGETS; do
	echo "  $HERE/work/$target/baseos-$target.img"
done
