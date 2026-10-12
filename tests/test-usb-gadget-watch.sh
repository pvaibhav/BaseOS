#!/bin/sh
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
: "${TMPDIR:=$HERE/work/test-tmp}"
mkdir -p "$TMPDIR"
T="$(mktemp -d "$TMPDIR/usb-watch.XXXXXX")"
trap 'rm -rf "$T"' EXIT HUP INT TERM
CC="${CC:-cc}"
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -o "$T/watch" "$HERE/src/usb-gadget-watch.c"
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -o "$T/events" "$HERE/tests/usb-gadget-watch-events.c"
SYS="$T/sys"
ROLE="$SYS/devices/platform/soc/usbc0"
G="$SYS/kernel/config/usb_gadget/g1"
mkdir -p "$ROLE" "$G/configs/c.1" "$SYS/class/udc/5100000.udc-controller"
# Opening these read-trigger controls would block the test. They are not status.
mkfifo "$ROLE/usb_device" "$ROLE/usb_host" "$ROLE/usb_null"
BASEOS_SYS_ROOT="$SYS" "$T/events"
once() { BASEOS_SYS_ROOT="$SYS" "$T/watch" --once; }
# An unbound gadget must stay untouched in every non-peripheral/unknown role.
for role in usb_host usb_null unexpected ''; do
    printf '%s\n' "$role" > "$ROLE/otg_role"
    printf '\n' > "$G/UDC"
    once
    [ "$(cat "$G/UDC")" = '' ]
done
rm "$ROLE/otg_role"
once
[ "$(cat "$G/UDC")" = '' ]
# Device mode recovers the binding without changing gadget configuration.
printf 'usb_device\n' > "$ROLE/otg_role"
echo preserved > "$G/configs/c.1/marker"
once
[ "$(cat "$G/UDC")" = 5100000.udc-controller ]
[ "$(cat "$G/configs/c.1/marker")" = preserved ]
# An existing binding, including another controller name, is left alone.
echo other-controller > "$G/UDC"
once
[ "$(cat "$G/UDC")" = other-controller ]
# Missing controller/gadget are silent no-ops, with no composition side effects.
printf '\n' > "$G/UDC"
rmdir "$SYS/class/udc/5100000.udc-controller"
once
[ "$(cat "$G/UDC")" = '' ]
rm "$G/UDC"
once
[ ! -e "$G/UDC" ]
echo "usb-gadget-watch tests passed"
