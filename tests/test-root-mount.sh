#!/bin/sh
# rcS's real root-filesystem block, run on files and command stubs: what a boot
# leaves read-only while still repairing recorded ext4 errors before leaving / ro,
# and what a legacy writable root leaves alone. Compatible with BusyBox ash.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
RCS="$HERE/overlay/etc/init.d/rcS"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
mkdir -p "$TMP/bin"
export ROOT_TEST_TMP="$TMP"
fail() { echo "FAIL: $*" >&2; exit 1; }

awk '/^# Root filesystem\. The vendor initramfs/{copy=1} /^# \/usr\/lib\. A composed image/{copy=0} copy' "$RCS" \
	| sed -e "s|/proc/mounts|$TMP/mounts|g" \
		-e "s|/sys/fs/ext4/mmcblk0p5/errors_count|$TMP/errors_count|g" \
		-e "s|/usr/sbin/e2fsck|$TMP/bin/e2fsck|g" \
		-e "s|/bin/mount |$TMP/bin/mount |g" \
		-e "s|/dev/kmsg|$TMP/kmsg|g" > "$TMP/root-block"
grep -q 'errors_count' "$TMP/root-block" || fail "rcS root block not found"
! grep -q 'remount,rw' "$TMP/root-block" || fail "rcS root block still remounts root rw"

cat > "$TMP/bin/mount" <<'EOF'
#!/bin/sh
printf 'mount %s\n' "$*" >> "$ROOT_TEST_TMP/events"
EOF
chmod 755 "$TMP/bin/mount"
fsck_stub() {
	printf '#!/bin/sh\nprintf "e2fsck %%s\\n" "$*" >> "$ROOT_TEST_TMP/events"\nexit %s\n' "$1" > "$TMP/bin/e2fsck"
	chmod 755 "$TMP/bin/e2fsck"
}
run() {
	rm -f "$TMP/events" "$TMP/kmsg"
	: > "$TMP/events"
	sh "$TMP/root-block"
}
kernel_root() {
	printf 'rootfs / rootfs rw 0 0\n/dev/root / ext4 ro,relatime,data=ordered 0 0\nproc /proc proc rw 0 0\n' > "$TMP/mounts"
}

# A root already mounted writable is left alone.
printf 'rootfs / rootfs rw 0 0\n/dev/mmcblk0p5 / ext4 rw,noatime,nodiratime,nobarrier,noauto_da_alloc,data=ordered 0 0\n' > "$TMP/mounts"
echo 0 > "$TMP/errors_count"
fsck_stub 0
run
[ ! -s "$TMP/events" ] || fail "writable root was touched: $(cat "$TMP/events")"
[ ! -e "$TMP/kmsg" ] || fail "writable root logged a message"
echo 'ok: a root the initramfs already mounted writable is left alone'

# Kernel-mounted, clean: stays read-only with no e2fsck or remount.
kernel_root
run
[ ! -s "$TMP/events" ] || fail "clean ro root ran unexpected commands: $(cat "$TMP/events")"
[ ! -e "$TMP/kmsg" ] || fail "clean ro boot logged a message"
echo 'ok: a clean kernel-mounted root stays read-only with zero work'

# Recorded errors: check and log, still staying read-only.
echo 3 > "$TMP/errors_count"
run
[ "$(cat "$TMP/events")" = 'e2fsck -y /dev/mmcblk0p5' ] || fail "flagged root: $(cat "$TMP/events")"
grep -q 'had 3 recorded errors; e2fsck exit 0' "$TMP/kmsg" || fail "check not reported"
for status in 1 3 4 8; do
	fsck_stub "$status"
	run
	[ "$(cat "$TMP/events")" = 'e2fsck -y /dev/mmcblk0p5' ] || fail "exit $status: $(cat "$TMP/events")"
	grep -q "e2fsck exit $status" "$TMP/kmsg" || fail "exit $status not reported"
done
echo 'ok: recorded errors run e2fsck, log exit status, and leave root read-only'

# Missing checker or unreadable counter does not fail.
rm "$TMP/bin/e2fsck"
run
[ ! -s "$TMP/events" ] || fail "missing checker ran commands: $(cat "$TMP/events")"
fsck_stub 1
rm "$TMP/errors_count"
run
[ ! -s "$TMP/events" ] || fail "missing counter ran commands: $(cat "$TMP/events")"
echo 'ok: missing checker or error counter leaves ro root cleanly intact'
echo 'root mount tests passed'
