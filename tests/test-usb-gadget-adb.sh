#!/bin/sh
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$HERE/overlay/usr/sbin/usb-gadget-adb"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

# Build a fake /sys tree: one UDC and an (empty) configfs usb_gadget dir. On
# macOS these are all just ordinary files/dirs, so the script's mkdir/echo
# against them work with no root and no Linux. Note the script deliberately does
# NOT touch usbc0/otg_role (writing it wedges the real vendor kernel), so the
# fake tree needs no role-manager node.
build_tree() {
	root="$1"
	mkdir -p "$root/class/udc/5100000.udc-controller" \
	         "$root/kernel/config/usb_gadget"
}

run_gadget() {
	sys="$1"
	BASEOS_SYS_ROOT="$sys" \
	BASEOS_FFS_DIR="$TMP/ffs" \
		sh "$SCRIPT"
}

# --- Happy path -----------------------------------------------------------
build_tree "$TMP/sys"
run_gadget "$TMP/sys"

G="$TMP/sys/kernel/config/usb_gadget/g1"

# Gadget attributes.
[ "$(cat "$G/idVendor")" = "0x18d1" ]
[ "$(cat "$G/idProduct")" = "0x4e42" ]
[ -s "$G/strings/0x409/serialnumber" ]
[ -s "$G/strings/0x409/manufacturer" ]
[ -s "$G/strings/0x409/product" ]
[ -d "$G/configs/c.1" ]
[ -d "$G/functions/ffs.adb" ]
[ -L "$G/configs/c.1/ffs.adb" ]

# Bound to the fake UDC.
[ "$(cat "$G/UDC")" = "5100000.udc-controller" ]

# --- Idempotent second run is a no-op -------------------------------------
# A bound gadget must be left untouched. Stamp a marker, re-run, confirm the
# gadget tree is byte-for-byte identical and the marker survives.
echo marker > "$G/strings/0x409/product"
before="$(find "$G" | sort; cat "$G/strings/0x409/product")"
run_gadget "$TMP/sys"
after="$(find "$G" | sort; cat "$G/strings/0x409/product")"
[ "$before" = "$after" ] || { echo "second run mutated the gadget" >&2; exit 1; }

# --- No UDC present exits 0 quickly ---------------------------------------
# An empty /sys (no udc, no configfs) must be a silent no-op.
mkdir -p "$TMP/sys2/class/udc"
run_gadget "$TMP/sys2"
[ ! -d "$TMP/sys2/kernel/config/usb_gadget/g1" ] \
	|| { echo "gadget composed with no UDC present" >&2; exit 1; }

echo "usb-gadget-adb tests passed"
