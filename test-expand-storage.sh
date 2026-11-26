#!/bin/sh
# Regression tests for first-boot expansion safety and queued boot diagnostics.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"

# Host-native: shell scripts and stand-ins only. The disposable block nodes are
# never opened: partition changes, formatting and mounts are all stubbed.
docker run --rm -i --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE/overlay/usr/sbin/expand-storage":/test/expand-storage:ro \
  -v "$HERE/assets/baseos.conf":/usr/share/baseos/baseos.conf:ro \
  -v "$HERE/overlay/usr/share/baseos/boot-log.sh":/usr/share/baseos/boot-log.sh:ro \
  alpine:3.20 sh -eu <<'TEST'
mkdir -p /usr/local/bin /usr/sbin /data /mnt/sdcard
export PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
mknod /dev/mmcblk0 b 7 0
mknod /dev/mmcblk0p7 b 7 1
QUEUE=/data/baseos-boot.log
RAM_LOG=/tmp/baseos-boot.log
printf 'Existing card contents\n' > /mnt/sdcard/untouched.txt
printf 'Add a frontend to this card.\n' > /usr/share/baseos/card-readme.txt

cat > /usr/sbin/gptgrow <<'EOF'
#!/bin/sh
[ "$#" -eq 1 ] && [ "$1" = /dev/mmcblk0 ] || exit 99
echo gptgrow >> /tmp/calls.log
echo 'gptgrow stdout'
echo 'gptgrow stderr' >&2
exit "$(cat /tmp/gptgrow-exit)"
EOF
cat > /usr/local/bin/mkfs.vfat <<'EOF'
#!/bin/sh
echo mkfs >> /tmp/calls.log
printf '%s\n' "$*" >> /tmp/mkfs.log
echo 'mkfs stdout'
echo 'mkfs stderr' >&2
exit "$(cat /tmp/mkfs-exit)"
EOF
cat > /usr/bin/baseos-splash <<'EOF'
#!/bin/sh
echo splash >> /tmp/calls.log
printf '%s\n' "$*" >> /tmp/splash.log
EOF
cat > /usr/local/bin/partprobe <<'EOF'
#!/bin/sh
[ "$#" -eq 1 ] && [ "$1" = /dev/mmcblk0 ] || exit 99
echo partprobe >> /tmp/calls.log
EOF
cat > /usr/local/bin/mount <<'EOF'
#!/bin/sh
[ "$*" = '-t vfat -o rw /dev/mmcblk0p7 /tmp/.cardnew' ] || exit 99
echo mount >> /tmp/calls.log
EOF
cat > /usr/local/bin/sync <<'EOF'
#!/bin/sh
echo sync >> /tmp/calls.log
EOF
cat > /usr/local/bin/umount <<'EOF'
#!/bin/sh
[ "$#" -eq 1 ] && [ "$1" = /tmp/.cardnew ] || exit 99
echo umount >> /tmp/calls.log
EOF
chmod 755 /usr/sbin/gptgrow /usr/bin/baseos-splash /usr/local/bin/*

reset_case() {
	rm -f /tmp/*.log "$QUEUE" /data/expand.log
	rm -rf /tmp/.cardnew
	echo 1 > /tmp/gptgrow-exit
	echo 0 > /tmp/mkfs-exit
}
run_expansion() {
	status=0
	/bin/sh /test/expand-storage > /tmp/console.log 2>&1 || status=$?
}
check_unmounted_card() {
	[ "$(cat /mnt/sdcard/untouched.txt)" = 'Existing card contents' ]
	[ "$(find /mnt/sdcard -type f)" = /mnt/sdcard/untouched.txt ]
	[ ! -e /data/expand.log ] && [ ! -e /tmp/expand.log ]
}
check_gptgrow_output() {
	grep -qx 'gptgrow stdout' "$1"
	grep -qx 'gptgrow stderr' "$1"
}

# An already-expanded card must never be formatted or show expansion progress.
# That is every ordinary boot, so it leaves the log alone.
reset_case
echo 'earlier boot diagnostics' > "$QUEUE"
run_expansion
[ "$status" -eq 0 ]
[ "$(cat /tmp/calls.log)" = gptgrow ]
[ ! -e /tmp/splash.log ] && [ ! -e /tmp/mkfs.log ]
[ "$(cat "$QUEUE")" = 'earlier boot diagnostics' ]
[ ! -e "$RAM_LOG" ]
check_unmounted_card
echo 'ok: an already-expanded card adds nothing to the log'

# First boot may format only after gptgrow confirms that it changed p7.
reset_case
echo 0 > /tmp/gptgrow-exit
run_expansion
[ "$status" -eq 0 ]
[ "$(cat /tmp/calls.log)" = "$(printf 'gptgrow\nsplash\npartprobe\nmkfs\nmount\nsync\numount\n')" ]
grep -qx -- '--important 45 EXPANDING STORAGE' /tmp/splash.log
grep -qx -- '-F 32 -n BASEOS /dev/mmcblk0p7' /tmp/mkfs.log
cmp /usr/share/baseos/card-readme.txt /tmp/.cardnew/README.txt
cmp /usr/share/baseos/baseos.conf /tmp/.cardnew/baseos.conf
check_gptgrow_output "$QUEUE"
grep -qx 'mkfs stdout' "$QUEUE"
grep -qx 'mkfs stderr' "$QUEUE"
grep -q ' storage expanded to fill the card; empty and ready for a frontend$' "$QUEUE"
[ ! -e "$RAM_LOG" ]
check_unmounted_card
echo 'ok: a newly-expanded card is formatted once and its output is queued'

# An ordinary boot preserves existing user configuration.
printf 'mdns=false\n' > /tmp/.cardnew/baseos.conf
echo 1 > /tmp/gptgrow-exit
run_expansion
[ "$status" -eq 0 ]
grep -qx 'mdns=false' /tmp/.cardnew/baseos.conf
echo 'ok: existing user configuration survives an ordinary boot'

# A real gptgrow error is never treated as permission to format the card.
reset_case
echo 2 > /tmp/gptgrow-exit
run_expansion
[ "$status" -eq 1 ]
[ "$(cat /tmp/calls.log)" = gptgrow ]
[ ! -e /tmp/splash.log ] && [ ! -e /tmp/mkfs.log ]
check_gptgrow_output "$QUEUE"
grep -q ' gptgrow failed (exit 2); leaving p7 untouched$' "$QUEUE"
check_unmounted_card
echo 'ok: partition-growth failure is logged without formatting or mounting'

# A failed format must not continue into card mounting or README installation.
reset_case
echo 0 > /tmp/gptgrow-exit
echo 9 > /tmp/mkfs-exit
run_expansion
[ "$status" -eq 1 ]
[ "$(cat /tmp/calls.log)" = "$(printf 'gptgrow\nsplash\npartprobe\nmkfs\n')" ]
grep -qx 'mkfs stderr' "$QUEUE"
grep -q ' mkfs.vfat failed; leaving p7 as-is$' "$QUEUE"
[ ! -e /tmp/.cardnew ]
check_unmounted_card
echo 'ok: format failure retains diagnostics and stops before mounting'

# A read-only bind mount reliably rejects append even as container root.
# Reusing it as the spool target verifies fallback without privileged mounts.
reset_case
readonly_hash=$(sha256sum /usr/share/baseos/boot-log.sh)
ln -s /usr/share/baseos/boot-log.sh "$QUEUE"
echo 2 > /tmp/gptgrow-exit
run_expansion
[ "$status" -eq 1 ]
[ "$(sha256sum /usr/share/baseos/boot-log.sh)" = "$readonly_hash" ]
[ "$(readlink "$QUEUE")" = /usr/share/baseos/boot-log.sh ]
check_gptgrow_output "$RAM_LOG"
grep -q ' gptgrow failed (exit 2); leaving p7 untouched$' "$RAM_LOG"
[ "$(cat /tmp/calls.log)" = gptgrow ]
check_unmounted_card
echo 'ok: an unwritable early spool falls back to RAM without changing expansion safety'

echo 'expansion safety and boot-log tests passed'
TEST
