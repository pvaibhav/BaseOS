#!/bin/sh
# Exercise the real BusyBox session with card, GPU and frontend stand-ins.
# All absolute runtime paths belong to the disposable container; no device,
# privileged mounts, frontend download or changes to another repo are needed.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"

docker run --rm -i --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE/overlay/usr/sbin/frontend-session":/usr/sbin/frontend-session:ro \
  -v "$HERE/overlay/usr/share/baseos/boot-log.sh":/usr/share/baseos/boot-log.sh:ro \
  alpine:3.20 sh -eu <<'TEST'
mkdir -p /usr/local/bin /usr/sbin /mnt/sdcard /data
export PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ln -s /mnt/sdcard /mnt/SDCARD
unset SLOT_ROOT
BOOT_LOG=/mnt/sdcard/baseos-boot.log
RAM_LOG=/tmp/baseos-boot.log
EXPANSION_LOG=/data/baseos-boot.log

cat > /usr/local/bin/mountpoint <<'EOF'
#!/bin/sh
echo mountpoint >> /tmp/mountpoint.log
[ ! -e /tmp/no-card ]
EOF
cat > /usr/local/bin/mount <<'EOF'
#!/bin/sh
echo mount >> /tmp/mount.log
exit 1
EOF
cat > /usr/local/bin/sleep <<'EOF'
#!/bin/sh
echo "$1" >> /tmp/sleep.log
if [ "$1" = 3600 ]; then
	# Stop the intentional USB-storage idle loop without waiting an hour.
	kill -TERM "$PPID"
elif [ "$1" = 0.1 ] && [ -f /tmp/gpu-ready-after ]; then
	if [ "$(wc -l < /tmp/sleep.log)" -ge "$(cat /tmp/gpu-ready-after)" ]; then
		: > /dev/mali0
	fi
fi
exit 0
EOF
cat > /usr/sbin/baseos-update <<'EOF'
#!/bin/sh
echo "$*" >> /tmp/update.log
EOF
cat > /usr/bin/baseos-splash <<'EOF'
#!/bin/sh
echo "$*" >> /tmp/splash.log
EOF

# An opaque, non-executable file stands in for the ELF. The loader stand-in
# checks that the session supplies the binary path, root and working directory;
# passing this file to /bin/sh or executing it directly cannot pass the test.
cat > /lib/ld-linux-aarch64.so.1 <<'EOF'
#!/bin/sh
env > /tmp/frontend-env.log
[ "$#" -eq 1 ] || exit 90
case "$1" in
/mnt/sdcard/System/frontend)
	[ "$(cat "$1")" = GENERIC-ELF-FIXTURE ] || exit 91
	. /tmp/generic-checks
	exit 0
	;;
/mnt/sdcard/System/slot) ;;
*) exit 90 ;;
esac
[ "$(cat "$1")" = SLOT-ELF-FIXTURE ] || exit 91
[ -s /run/boot-frontend-exec ] || exit 92
[ "$(cat /tmp/update.log)" = confirm ] || exit 93
printf 'slot\nroot=%s\ncwd=%s\n' "$SLOT_ROOT" "$PWD" > /tmp/frontend.log
echo 'slot stdout'
echo 'slot stderr' >&2
if [ -f /tmp/slot-exit ]; then exit "$(cat /tmp/slot-exit)"; fi
EOF
cat > /tmp/nextui-launch <<'EOF'
#!/bin/sh
env > /tmp/frontend-env.log
[ -s /run/boot-frontend-exec ] || exit 92
[ "$(cat /tmp/update.log)" = confirm ] || exit 93
printf 'nextui\nroot=%s\n' "${SLOT_ROOT-unset}" > /tmp/frontend.log
EOF
cat > /tmp/spruce-runtime <<'EOF'
#!/bin/sh
env > /tmp/frontend-env.log
[ "$0" = /mnt/SDCARD/spruce/scripts/runtime.sh ] || exit 90
[ -s /run/boot-frontend-exec ] || exit 92
[ "$(cat /tmp/update.log)" = confirm ] || exit 93
. /mnt/SDCARD/spruce/scripts/helper.sh
printf 'spruce\nroot=%s\nhelper=%s\n' "${SLOT_ROOT-unset}" "$SPRUCE_HELPER" > /tmp/frontend.log
if [ -f /tmp/spruce-exit ]; then exit "$(cat /tmp/spruce-exit)"; fi
EOF
cat > /tmp/generic-checks <<'EOF'
env > /tmp/frontend-env.log
[ -s /run/boot-frontend-exec ] || exit 92
[ "$(cat /tmp/update.log)" = confirm ] || exit 93
[ "$PWD" = /mnt/sdcard ] || exit 94
[ "${SLOT_ROOT-unset}" = unset ] || exit 95
printf 'generic\n' > /tmp/frontend.log
echo 'generic stdout'
echo 'generic stderr' >&2
if [ -f /tmp/generic-exit ]; then exit "$(cat /tmp/generic-exit)"; fi
EOF
cat > /tmp/generic-launch <<'EOF'
#!/bin/sh
[ "$0" = /mnt/sdcard/System/launch_frontend.sh ] || exit 90
. /tmp/generic-checks
echo script >> /tmp/frontend.log
EOF
cat > /tmp/installer <<'EOF'
#!/bin/sh
echo install >> /tmp/install.log
echo 'installer stdout'
echo 'installer stderr' >&2
if [ -f /tmp/installer-exit ]; then exit "$(cat /tmp/installer-exit)"; fi
mkdir -p /mnt/sdcard/.system/h700/paks/MinUI.pak
cp /tmp/nextui-launch /mnt/sdcard/.system/h700/paks/MinUI.pak/launch.sh
EOF
cat > /usr/local/bin/unzip <<'EOF'
#!/bin/sh
[ "$#" -eq 5 ] && [ "$1" = -o ] && [ "$2" = /mnt/sdcard/MinUI.zip ] \
	&& [ "$3" = '.tmp_update/*' ] && [ "$4" = -d ] && [ "$5" = /mnt/sdcard ] \
	|| exit 94
echo unzip >> /tmp/unzip.log
echo 'unzip stdout'
echo 'unzip stderr' >&2
mkdir -p /mnt/sdcard/.tmp_update
cp /tmp/installer /mnt/sdcard/.tmp_update/h700.sh
EOF
chmod 755 /usr/local/bin/* /usr/sbin/baseos-update /usr/bin/baseos-splash \
  /lib/ld-linux-aarch64.so.1
chmod 644 /tmp/nextui-launch /tmp/installer /tmp/spruce-runtime /tmp/generic-launch

reset() {
	rm -rf /mnt/sdcard
	mkdir -p /mnt/sdcard
	rm -f /tmp/*.log /tmp/no-card /tmp/gpu-ready-after /tmp/slot-exit /tmp/spruce-exit /tmp/installer-exit \
		"$EXPANSION_LOG" /tmp/generic-exit \
		/run/boot-frontend-exec /run/usb-storage-device \
		/run/usb-storage-ready /run/usb-storage-failed
	: > /dev/mali0
}
slot_card() {
	mkdir -p /mnt/sdcard/System
	echo SLOT-ELF-FIXTURE > /mnt/sdcard/System/slot
	chmod 644 /mnt/sdcard/System/slot
}
nextui_card() {
	mkdir -p /mnt/sdcard/.system/h700/paks/MinUI.pak
	cp /tmp/nextui-launch /mnt/sdcard/.system/h700/paks/MinUI.pak/launch.sh
}
spruce_card() {
	mkdir -p /mnt/sdcard/spruce/scripts
	cp /tmp/spruce-runtime /mnt/sdcard/spruce/scripts/runtime.sh
	echo 'SPRUCE_HELPER=loaded' > /mnt/sdcard/spruce/scripts/helper.sh
}
generic_card() {
	mkdir -p /mnt/sdcard/System
	if [ "$1" = binary ]; then
		echo GENERIC-ELF-FIXTURE > /mnt/sdcard/System/frontend
		chmod 644 /mnt/sdcard/System/frontend
	else
		cp /tmp/generic-launch /mnt/sdcard/System/launch_frontend.sh
	fi
}
run_session() {
	status=0
	timeout 5 sh /usr/sbin/frontend-session > /tmp/console.log 2>&1 || status=$?
}
no_legacy_logs() {
	[ ! -e /tmp/nextui-session.log ] \
		&& [ ! -e /tmp/expand.log ] && [ ! -e /mnt/sdcard/baseos-session.log ]
}
card_snapshot() {
	find /mnt/sdcard -type f -exec sha256sum {} \; | sort
}
check_handoff_log() {
	[ -s /run/boot-frontend-exec ]
	grep -Eq '^[0-9.]+ Startup: ([0-9]+\.[0-9]{2} s before Linux, )?[0-9]+\.[0-9]{2} s Linux to menu(, [0-9]+\.[0-9]{2} s total)? \((Slot|MinUI|spruceOS|custom frontend)(, after [0-9.]+ s waiting for the GPU)?\)$' "$1"
	[ "$(grep -c ' Startup: ' "$1")" -eq "$2" ]
}

reset
slot_card
echo 'previous boot diagnostics' > "$BOOT_LOG"
echo 'early expansion diagnostics' > "$EXPANSION_LOG"
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/frontend.log)" = "$(printf 'slot\nroot=/mnt/sdcard\ncwd=/mnt/sdcard\n')" ]
[ ! -x /mnt/sdcard/System/slot ]
[ "$(head -n 2 "$BOOT_LOG")" = "$(printf 'previous boot diagnostics\nearly expansion diagnostics\n')" ]
[ "$(cat /tmp/slot.log)" = "$(printf 'slot stdout\nslot stderr\n')" ]
! grep -qE '^slot (stdout|stderr)$' "$BOOT_LOG"
check_handoff_log "$BOOT_LOG" 1
[ ! -e "$EXPANSION_LOG" ] && [ ! -e "$RAM_LOG" ]
[ ! -e /tmp/splash.log ] && [ ! -e /tmp/sleep.log ]
[ "$(find /mnt/sdcard -type f | wc -l)" -eq 2 ]
no_legacy_logs
echo 'ok: Slot handoff uses the shared boot log while frontend output stays in RAM'

# A respawn must preserve the first handoff timestamp, append BaseOS diagnostics,
# replace the raw Slot output, and propagate the frontend exit status to init.
first_handoff=$(cat /run/boot-frontend-exec)
echo 'earlier session diagnostics' >> "$BOOT_LOG"
echo stale >> /tmp/slot.log
echo 42 > /tmp/slot-exit
rm /tmp/update.log
run_session
[ "$status" -eq 42 ]
[ "$(cat /run/boot-frontend-exec)" = "$first_handoff" ]
check_handoff_log "$BOOT_LOG" 1
grep -qx 'earlier session diagnostics' "$BOOT_LOG"
[ "$(cat /tmp/slot.log)" = "$(printf 'slot stdout\nslot stderr\n')" ]
[ "$(grep -c ' Frontend started again: Slot$' "$BOOT_LOG")" -eq 1 ]
! grep -qE '^slot (stdout|stderr)$' "$BOOT_LOG"
[ "$(grep -c '^early expansion diagnostics$' "$BOOT_LOG")" -eq 1 ]
no_legacy_logs
echo 'ok: respawn preserves one boot timing record and refreshes Slot diagnostics'

# Simulate a later boot by clearing volatile state while retaining the card.
rm /run/boot-frontend-exec /tmp/update.log /tmp/slot-exit
run_session
[ "$status" -eq 0 ]
check_handoff_log "$BOOT_LOG" 2
grep -qx 'previous boot diagnostics' "$BOOT_LOG"
[ "$(grep -c -e ' Startup: .*(Slot)$' -e ' Frontend started again: Slot$' "$BOOT_LOG")" -eq 3 ]
echo 'ok: a later boot appends its timing without discarding prior boot logs'

# The bind-mounted helper is read-only even for container root. An existing
# log symlink to it makes append fail without relying on chmod permissions.
reset
slot_card
readonly_hash=$(sha256sum /usr/share/baseos/boot-log.sh)
ln -s /usr/share/baseos/boot-log.sh "$BOOT_LOG"
echo 'unflushed expansion diagnostics' > "$EXPANSION_LOG"
run_session
[ "$status" -eq 0 ]
[ "$(sha256sum /usr/share/baseos/boot-log.sh)" = "$readonly_hash" ]
[ "$(readlink "$BOOT_LOG")" = /usr/share/baseos/boot-log.sh ]
[ "$(cat "$EXPANSION_LOG")" = 'unflushed expansion diagnostics' ]
[ "$(cat /tmp/slot.log)" = "$(printf 'slot stdout\nslot stderr\n')" ]
check_handoff_log "$RAM_LOG" 1
no_legacy_logs
echo 'ok: an unwritable card log falls back to RAM without losing the expansion spool'

reset
spruce_card
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/frontend.log)" = "$(printf 'spruce\nroot=unset\nhelper=loaded\n')" ]
[ ! -x /mnt/SDCARD/spruce/scripts/runtime.sh ]
[ ! -e /tmp/slot.log ] && [ ! -e /tmp/splash.log ]
check_handoff_log "$BOOT_LOG" 1
first_handoff=$(cat /run/boot-frontend-exec)
echo 42 > /tmp/spruce-exit
rm /tmp/update.log
run_session
[ "$status" -eq 42 ]
[ "$(cat /run/boot-frontend-exec)" = "$first_handoff" ]
check_handoff_log "$BOOT_LOG" 1
echo 'ok: spruceOS shell handoff resolves card helpers and preserves respawn exit status'

reset
spruce_card
slot_card
run_session
[ "$status" -eq 0 ]
grep -qx slot /tmp/frontend.log
echo 'ok: Slot retains priority over spruceOS'

reset
spruce_card
rm /dev/mali0
echo 3 > /tmp/gpu-ready-after
run_session
[ "$status" -eq 0 ]
grep -qx spruce /tmp/frontend.log
[ "$(grep -c '^0.1$' /tmp/sleep.log)" -eq 3 ]
echo 'ok: spruceOS waits for GPU readiness before handoff'

reset
spruce_card
: > /mnt/sdcard/MinUI.zip
run_session
[ "$status" -eq 0 ]
grep -qx nextui /tmp/frontend.log
grep -qx install /tmp/install.log
echo 'ok: NextUI installer retains priority over spruceOS'

reset
nextui_card
spruce_card
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/frontend.log)" = "$(printf 'nextui\nroot=unset\n')" ]
[ ! -e /tmp/slot.log ] && [ ! -e /tmp/splash.log ]
check_handoff_log "$BOOT_LOG" 1
slot_card
rm /tmp/update.log
run_session
[ "$status" -eq 0 ]
grep -qx slot /tmp/frontend.log
check_handoff_log "$BOOT_LOG" 1
echo 'ok: Slot wins over NextUI, which wins over spruceOS'

reset
slot_card
: > /mnt/sdcard/MinUI.zip
run_session
[ "$status" -eq 0 ]
grep -qx slot /tmp/frontend.log
grep -qx unzip /tmp/unzip.log
grep -qx install /tmp/install.log
grep -qx -- '--important 85 INSTALLING FRONTEND' /tmp/splash.log
grep -qx 'unzip stdout' "$BOOT_LOG"
grep -qx 'unzip stderr' "$BOOT_LOG"
grep -qx 'installer stdout' "$BOOT_LOG"
grep -qx 'installer stderr' "$BOOT_LOG"
check_handoff_log "$BOOT_LOG" 1
no_legacy_logs
echo 'ok: NextUI archive installs before Slot handoff'

reset
nextui_card
slot_card
mkdir -p /mnt/sdcard/.tmp_update
cp /tmp/installer /mnt/sdcard/.tmp_update/h700.sh
: > /mnt/sdcard/nextui.update.pakz
run_session
[ "$status" -eq 0 ]
grep -qx slot /tmp/frontend.log
grep -qx install /tmp/install.log
[ ! -e /tmp/unzip.log ]
grep -qx -- '--important 85 UPDATING FRONTEND' /tmp/splash.log
grep -qx 'installer stderr' "$BOOT_LOG"
echo 'ok: NextUI pakz updates still run before handoff'

reset
: > /mnt/sdcard/MinUI.zip
echo 7 > /tmp/installer-exit
run_session
[ "$status" -eq 1 ]
grep -qx 'installer stdout' "$BOOT_LOG"
grep -qx 'installer stderr' "$BOOT_LOG"
grep -q ' installer exited 7$' "$BOOT_LOG"
[ "$(tail -n 1 /tmp/splash.log)" = '--important -1 INSTALL FAILED' ]
[ ! -e /run/boot-frontend-exec ] && [ ! -e /tmp/frontend.log ]
no_legacy_logs
echo 'ok: a failing installer keeps stderr and exit status in the shared log'

reset
slot_card
rm /dev/mali0
echo 3 > /tmp/gpu-ready-after
run_session
[ "$status" -eq 0 ] && [ -s /tmp/frontend.log ]
[ "$(grep -c '^0.1$' /tmp/sleep.log)" -eq 3 ]
reset
slot_card
rm /dev/mali0
run_session
[ "$status" -eq 0 ] && [ -s /tmp/frontend.log ]
[ "$(grep -c '^0.1$' /tmp/sleep.log)" -eq 30 ]
echo 'ok: Slot shares the bounded GPU readiness wait'

# Both generic forms share card-root cwd, output handling, GPU wait and respawn.
for kind in binary script; do
	reset
	generic_card "$kind"
	rm /dev/mali0
	echo 3 > /tmp/gpu-ready-after
	run_session
	[ "$status" -eq 0 ]
	grep -qx generic /tmp/frontend.log
	[ "$(grep -c '^0.1$' /tmp/sleep.log)" -eq 3 ]
	[ "$(cat /tmp/generic.log)" = "$(printf 'generic stdout\ngeneric stderr\n')" ]
	! grep -qE '^generic (stdout|stderr)$' "$BOOT_LOG"
	[ ! -e /tmp/splash.log ] && [ ! -e /tmp/slot.log ]
	check_handoff_log "$BOOT_LOG" 1
	if [ "$kind" = script ]; then
		grep -qx script /tmp/frontend.log
		[ ! -x /mnt/sdcard/System/launch_frontend.sh ]
	else
		[ ! -x /mnt/sdcard/System/frontend ]
		# The binary wins even when a shell launcher is also installed.
		generic_card script
	fi
	first_handoff=$(cat /run/boot-frontend-exec)
	echo stale >> /tmp/generic.log
	echo 42 > /tmp/generic-exit
	rm /tmp/update.log
	run_session
	[ "$status" -eq 42 ]
	[ "$(cat /run/boot-frontend-exec)" = "$first_handoff" ]
	check_handoff_log "$BOOT_LOG" 1
	[ "$(cat /tmp/generic.log)" = "$(printf 'generic stdout\ngeneric stderr\n')" ]
	[ "$(cat /tmp/frontend.log)" = generic ]
	echo "ok: generic $kind handoff, output, GPU wait and respawn"
done

reset
generic_card binary
generic_card script
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/frontend.log)" = generic ]
echo 'ok: generic binary takes priority over shell launcher'

# A directory is not a binary; still allow the shell launcher.
reset
generic_card script
mkdir /mnt/sdcard/System/frontend
run_session
[ "$status" -eq 0 ]
grep -qx script /tmp/frontend.log
echo 'ok: generic script runs when binary path is a directory'

for kind in binary script; do
	reset
	generic_card "$kind"
	nextui_card
	slot_card
	spruce_card
	run_session
	[ "$status" -eq 0 ]
	grep -qx generic /tmp/frontend.log
	[ -e /tmp/generic.log ] && [ ! -e /tmp/slot.log ]
done
echo 'ok: both generic launch methods take priority over all named frontends'

reset
generic_card binary
: > /mnt/sdcard/MinUI.zip
run_session
[ "$status" -eq 0 ]
grep -qx generic /tmp/frontend.log
grep -qx install /tmp/install.log
echo 'ok: pending NextUI install runs before generic frontend handoff'

reset
generic_card binary
generic_card script
: > /tmp/no-card
run_session
[ "$status" -eq 1 ]
[ ! -e /tmp/frontend.log ] && [ ! -e /tmp/generic.log ]
[ ! -e /run/boot-frontend-exec ]
grep -qx -- '--important -1 INSERT SD CARD' /tmp/splash.log
echo 'ok: generic frontends do not run without a mounted card'

reset
# Directories at any entry point must not be mistaken for a frontend.
mkdir -p /mnt/sdcard/System/slot /mnt/sdcard/.system/h700/paks/MinUI.pak/launch.sh \
	/mnt/sdcard/spruce/scripts/runtime.sh \
	/mnt/sdcard/System/frontend /mnt/sdcard/System/launch_frontend.sh
run_session
[ "$status" -eq 1 ]
grep -qx -- '--important -1 ADD FRONTEND TO SD CARD' /tmp/splash.log
[ ! -e /run/boot-frontend-exec ] && [ ! -e /tmp/frontend.log ]
echo 'ok: missing frontend retains the add-frontend prompt'

reset
: > /mnt/sdcard/broken.pakz
run_session
[ "$status" -eq 1 ]
[ "$(tail -n 1 /tmp/splash.log)" = '--important -1 INSTALL FAILED' ]
[ ! -e /run/boot-frontend-exec ] && [ ! -e /tmp/frontend.log ]
echo 'ok: an incomplete NextUI installation retains its failure prompt'

reset
spruce_card
: > /tmp/no-card
echo 'pending expansion diagnostics' > "$EXPANSION_LOG"
before_card=$(card_snapshot)
run_session
[ "$status" -eq 1 ]
grep -qx -- '--important -1 INSERT SD CARD' /tmp/splash.log
[ ! -e /run/boot-frontend-exec ] && [ ! -e /tmp/frontend.log ]
[ "$(grep -c '^1$' /tmp/sleep.log)" -eq 15 ]
[ "$(card_snapshot)" = "$before_card" ]
[ ! -e "$BOOT_LOG" ]
[ "$(cat "$EXPANSION_LOG")" = 'pending expansion diagnostics' ]
grep -q ' no card mounted after 15s$' "$RAM_LOG"
grep -q ' no card mounted — waiting for insertion$' "$RAM_LOG"
no_legacy_logs
echo 'ok: a missing card uses RAM logging and leaves card files and expansion diagnostics untouched'

for state in ready failed; do
	reset
	slot_card
	nextui_card
	spruce_card
	generic_card binary
	generic_card script
	: > /mnt/sdcard/MinUI.zip
	echo /dev/mmcblk1 > /run/usb-storage-device
	: > "/run/usb-storage-$state"
	echo 'pending expansion diagnostics' > "$EXPANSION_LOG"
	before_card=$(card_snapshot)
	run_session
	[ "$status" -eq 143 ]
	[ ! -e /tmp/frontend.log ] && [ ! -e /tmp/install.log ]
	[ ! -e /tmp/unzip.log ] && [ ! -e /run/boot-frontend-exec ]
	[ ! -e /tmp/mountpoint.log ] && [ ! -e /tmp/mount.log ]
	[ "$(card_snapshot)" = "$before_card" ]
	[ ! -e "$BOOT_LOG" ]
	[ "$(cat "$EXPANSION_LOG")" = 'pending expansion diagnostics' ]
	grep -q ' USB storage mode — frontend intentionally stopped$' "$RAM_LOG"
	no_legacy_logs
	if [ "$state" = ready ]; then
		grep -qx -- '--important -1 USB STORAGE: EJECT BEFORE RESTART' /tmp/splash.log
	else
		grep -qx -- '--important -1 USB STORAGE FAILED: POWER OFF' /tmp/splash.log
		grep -q ' USB storage gadget did not become ready$' "$RAM_LOG"
	fi
done
echo 'ok: USB storage mode blocks card access, installers and all frontends'

# A validated target supplies both clocks; preserve the legacy BOOTTIME marker
# and the first counter sample across frontend respawns.
reset
nextui_card
cat > /usr/sbin/boot-clock <<'EOF'
#!/bin/sh
echo called >> /tmp/counter-calls
echo '2.31 3.414 2.319 5.733'
EOF
chmod 755 /usr/sbin/boot-clock
run_session
[ "$status" -eq 0 ]
[ "$(cat /run/boot-frontend-exec)" = 2.31 ]
[ "$(cat /run/boot-clock-handoff)" = '2.31 3.414 2.319 5.733' ]
check_handoff_log "$BOOT_LOG" 1
grep -Eqx '[0-9.]+ Startup: 3\.41 s before Linux, 2\.32 s Linux to menu, 5\.73 s total \(MinUI\)' "$BOOT_LOG"
rm /tmp/update.log
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/counter-calls)" = called ]
[ "$(grep -c ' s before Linux, ' "$BOOT_LOG")" -eq 1 ]
echo 'ok: counter stages retain BOOTTIME compatibility and survive respawns'

# Unavailable counters must not prevent the frontend from starting or emit a
# misleading partial stage record. Earlier cases cover an absent helper.
reset
nextui_card
cat > /usr/sbin/boot-clock <<'EOF'
#!/bin/sh
echo 'partial'
exit 1
EOF
run_session
[ "$status" -eq 0 ]
check_handoff_log "$BOOT_LOG" 1
! grep -q ' s before Linux, ' "$BOOT_LOG"
echo 'ok: failed counter sampling falls back to uptime-only handoff'
# The diagnostics helper starts once per boot, in the background, after the
# first handoff, with the log the session chose. Respawns do not start it again.
reset
slot_card
rm -f /usr/sbin/boot-clock
cat > /usr/sbin/baseos-diag <<'EOF'
#!/bin/sh
printf '%s %s\n' "$*" "$BASEOS_BOOT_LOG" >> /tmp/diag.log
EOF
chmod 755 /usr/sbin/baseos-diag
run_session
[ "$status" -eq 0 ]
n=0
while [ ! -s /tmp/diag.log ] && [ "$n" -lt 50 ]; do /bin/sleep 0.1; n=$((n + 1)); done
[ "$(cat /tmp/diag.log)" = "--boot $BOOT_LOG" ]
rm /tmp/update.log
run_session
/bin/sleep 0.3
[ "$(wc -l < /tmp/diag.log)" -eq 1 ]
! grep -q BASEOS_BOOT_LOG /tmp/frontend-env.log
rm /usr/sbin/baseos-diag
echo 'ok: diagnostics start once per boot, after the first handoff'

# The migration guard must short-circuit before executing its helper on all
# later boots. A pending migration runs before confirming a rootfs update.
reset
nextui_card
cat > /usr/sbin/baseos-boot-migrate <<'EOF'
#!/bin/sh
[ ! -e /tmp/update.log ] || exit 90
echo called >> /tmp/migration.log
[ ! -e /tmp/migration-fail ]
EOF
chmod 755 /usr/sbin/baseos-boot-migrate
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/migration.log)" = called ]
[ "$(cat /tmp/frontend.log)" = "$(printf 'nextui\nroot=unset\n')" ]
# A card that completed the earlier gzip migration still runs the helper: only
# the boot-pair-v2 marker means the faster pair has been dealt with.
reset
nextui_card
mkdir -p /data/boot-gzip-v1
echo installed > /data/boot-gzip-v1/complete
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/migration.log)" = called ]
[ "$(cat /data/boot-gzip-v1/complete)" = installed ]
# Any completion text ends it, including a result that carries a note.
for result in installed already-installed rolled-back 'skipped: target mismatch' \
	'installed; boot0 left unchanged: unexpected magic'; do
	reset
	nextui_card
	mkdir -p /data/boot-pair-v2
	echo "$result" > /data/boot-pair-v2/complete
	run_session
	[ "$status" -eq 0 ] && [ ! -e /tmp/migration.log ]
	[ "$(cat /tmp/frontend.log)" = "$(printf 'nextui\nroot=unset\n')" ]
done
rm -r /data/boot-pair-v2 /data/boot-gzip-v1
reset
nextui_card
echo /dev/mmcblk1 > /run/usb-storage-device
run_session
[ "$status" -eq 143 ] && [ ! -e /tmp/migration.log ]
reset
nextui_card
touch /tmp/migration-fail
run_session
[ "$status" -eq 143 ]
[ ! -e /tmp/update.log ] && [ ! -e /tmp/frontend.log ]
echo 'ok: migration precedes confirmation; completion and maintenance skip it; recovery failure blocks startup'

# No dynamic loader means the /usr/lib image did not mount. Such a boot must
# not confirm an update trial: the rollback counts exactly these boots.
rm -f /usr/sbin/baseos-boot-migrate /tmp/migration-fail
reset
nextui_card
mv /lib/ld-linux-aarch64.so.1 /tmp/loader
run_session
[ ! -e /tmp/update.log ]
mv /tmp/loader /lib/ld-linux-aarch64.so.1
reset
nextui_card
run_session
[ "$status" -eq 0 ] && [ "$(cat /tmp/update.log)" = confirm ]
echo 'ok: a boot without the dynamic loader does not confirm an update trial'

# Earlier cases run without a release file. When present, all launch methods
# inherit its values, including quoted, empty and future metadata fields.
cat > /etc/baseos-release <<'EOF'
# Generated release metadata fixture.
BASEOS=1
BASEOS_PLATFORM=h700
BASEOS_VERSION=0.1.0
BASEOS_BUILD=test-build
BASEOS_TARGET=rg34xxsp
BASEOS_DEVICE=rg34xx
BASEOS_MODEL="Anbernic RG34XX SP"
BASEOS_MODEL_STRING=RG34xxSP
BASEOS_PANEL_ROTATION_CCW=90
BASEOS_WIFI=1
BASEOS_BLUETOOTH=1
BASEOS_FUTURE_FIELD="future value with spaces"
BASEOS_OPTIONAL=
EOF
cat > /tmp/expected-release-env <<'EOF'
BASEOS=1
BASEOS_PLATFORM=h700
BASEOS_VERSION=0.1.0
BASEOS_BUILD=test-build
BASEOS_TARGET=rg34xxsp
BASEOS_DEVICE=rg34xx
BASEOS_MODEL=Anbernic RG34XX SP
BASEOS_MODEL_STRING=RG34xxSP
BASEOS_PANEL_ROTATION_CCW=90
BASEOS_WIFI=1
BASEOS_BLUETOOTH=1
BASEOS_FUTURE_FIELD=future value with spaces
BASEOS_OPTIONAL=
EOF
LC_ALL=C sort /tmp/expected-release-env > /tmp/expected-release-env.sorted
for frontend in slot nextui spruce generic-binary generic-script; do
	reset
	case "$frontend" in
		slot) slot_card ;;
		nextui) nextui_card ;;
		spruce) spruce_card ;;
		generic-binary) generic_card binary ;;
		generic-script) generic_card script ;;
	esac
	BASEOS_TARGET=stale BASEOS_MODEL=stale run_session
	[ "$status" -eq 0 ]
	grep -E '^BASEOS(_[A-Z0-9_]+)?=' /tmp/frontend-env.log \
		| LC_ALL=C sort > /tmp/actual-release-env
	diff -u /tmp/expected-release-env.sorted /tmp/actual-release-env
	! grep -Eq '^(SD|LAUNCH|FRONTEND|LAUNCH_TYPE|INSTALL_FAILED|CARD_MISSING|LOG)=' \
		/tmp/frontend-env.log
	echo "ok: $frontend inherits all release metadata without session-local variables"
done
echo 'frontend session tests passed'
TEST
