#!/bin/sh
# Parser and actual boot/update mount blocks, run on files and command stubs.
# Compatible with BusyBox ash; no device, privileges or real hostname changes.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$HERE/overlay/usr/sbin/baseos-config"
RCS="$HERE/overlay/etc/init.d/rcS"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
mkdir -p "$TMP/run" "$TMP/bin" "$TMP/sdcard" "$TMP/tf1-data" "$TMP/system"
export BASEOS_SHADOW_DEFAULT="$HERE/overlay/etc/shadow"
export BASEOS_MKPASSWD_BIN="$TMP/bin/mkpasswd"
export BASEOS_RUN_ROOT="$TMP/run"
export BASEOS_RELEASE_FILE="$TMP/release"
export BASEOS_HOSTNAME_BIN="$TMP/bin/hostname"
export BASEOS_TEST_LOG="$TMP/events"
export BASEOS_TEST_TMP="$TMP"
export PATH="$TMP/bin:$PATH"
printf 'BASEOS_TARGET=RG34xxSP\nBASEOS_MODEL_STRING=RG34xxSP\n' > "$TMP/release"

fail() { echo "FAIL: $*" >&2; exit 1; }
cat > "$TMP/bin/hostname" <<'EOF'
#!/bin/sh
[ "$#" -eq 1 ] || exit 1
printf 'hostname %s\n' "$1" >> "$BASEOS_TEST_LOG"
EOF
chmod 755 "$TMP/bin/hostname"

cat > "$TMP/bin/mkpasswd" <<'EOF'
#!/bin/sh
[ "$*" = '-m sha512 -P 0' ] || exit 1
cat > "$BASEOS_TEST_TMP/password-input"
printf '$6$fixture$hash\n'
EOF
chmod 755 "$TMP/bin/mkpasswd"

reset_runtime() { rm -f "$TMP/run"/*; : > "$TMP/events"; }
apply() { reset_runtime; sh "$SCRIPT" "$@"; }
check_runtime() {
	[ "$(cat "$TMP/run/hostname")" = "$1" ] || fail "runtime hostname: $1"
	printf '127.0.0.1 localhost %s\n::1 localhost\n' "$1" > "$TMP/want"
	cmp -s "$TMP/want" "$TMP/run/hosts" || fail "runtime hosts: $1"
	printf 'hostname=%s\nmdns=%s\nheadphone_pop_fix=%s\n' "$1" "$2" "${3:-false}" > "$TMP/want"
	cmp -s "$TMP/want" "$TMP/run/baseos.conf" || fail "effective settings: $1/$2"
	[ "$(grep '^hostname ' "$TMP/events")" = "hostname $1" ] \
		|| fail "hostname must be applied exactly once"
}

# Defaults apply for missing files, missing keys and commented template values.
apply
check_runtime rg34xxsp true
apply "$TMP/missing"
check_runtime rg34xxsp true
apply "$HERE/assets/baseos.conf"
check_runtime rg34xxsp true
printf 'mdns=false\n' > "$TMP/config"
apply "$TMP/config"
check_runtime rg34xxsp false
printf 'hostname=BaseOS\n' > "$TMP/config"
apply "$TMP/config"
check_runtime BaseOS true

# Headphone retention is strictly opt-in; malformed later duplicates reset it.
printf ' headphone_pop_fix = true # opt in\r\n' > "$TMP/config"
apply "$TMP/config"
check_runtime rg34xxsp true true
for value in false '' True 1 yes 'true false' '$(touch config-executed)'; do
	printf 'headphone_pop_fix=true\nheadphone_pop_fix=%s\n' "$value" > "$TMP/config"
	(cd "$TMP"; apply "$TMP/config")
	check_runtime rg34xxsp true false
done
[ ! -e "$TMP/config-executed" ] || fail "headphone setting executed shell code"

# Passwords are literal data, including #, = and shell metacharacters.
printf '%s\r\n' 'ssh_password=  secret#=$(touch nope)  ' > "$TMP/config"
apply "$TMP/config"
check_runtime rg34xxsp true
[ "$(cat "$TMP/password-input")" = 'secret#=$(touch nope)' ] || fail 'password parsing'
grep -q '^root:\$6\$fixture\$hash:' "$TMP/run/shadow" || fail 'root hash'
[ "$(ls -l "$TMP/run/shadow" | cut -c1-10)" = '-rw-------' ] || fail 'shadow permissions'
grep -v '^root:' "$BASEOS_SHADOW_DEFAULT" > "$TMP/accounts-want"
grep -v '^root:' "$TMP/run/shadow" > "$TMP/accounts-got"
cmp "$TMP/accounts-want" "$TMP/accounts-got" || fail 'other accounts changed'
printf 'ssh_password=first\nssh_password=\n' > "$TMP/config"
apply "$TMP/config"
cmp "$BASEOS_SHADOW_DEFAULT" "$TMP/run/shadow" || fail 'empty restores default'
apply
cmp "$BASEOS_SHADOW_DEFAULT" "$TMP/run/shadow" || fail 'missing restores default'

# The exact model ID preserves variant names and is normalized to lowercase.
for target in $(sed -n 's/      "id": "\([^"]*\)",/\1/p' "$HERE/devices.json"); do
	printf 'BASEOS_TARGET=%s\n' "$(printf '%s' "$target" | tr 'a-z' 'A-Z')" > "$TMP/release"
	apply
	check_runtime "$target" true
done
printf 'BASEOS_TARGET=bad model\n' > "$TMP/release"
apply
check_runtime baseos true
rm "$TMP/release"
apply
check_runtime baseos true
printf 'BASEOS_TARGET=RG34xxSP\n' > "$TMP/release"

# Whitespace/comments/CRLF work without accepting internal hostname whitespace.
printf ' # comment\r\n\thostname \t= My-RG34xxSP \t# name\r\n mdns = false\r\nunknown=ignored\r\n' > "$TMP/config"
cp "$TMP/config" "$TMP/original"
apply "$TMP/config"
check_runtime My-RG34xxSP false
cmp -s "$TMP/config" "$TMP/original" || fail "card settings were modified"
printf 'hostname=LastLine' > "$TMP/config"
apply "$TMP/config"
check_runtime LastLine true

name63="$(printf '%063d' 0 | tr 0 a)"
printf 'hostname=%s\n' "$name63" > "$TMP/config"
apply "$TMP/config"
check_runtime "$name63" true
for invalid in '' '-bad' 'bad-' '--help' 'two words' 'two	tabs' 'bad.name' \
	'bad_name' 'bad/name' 'bad=name' 'é' "${name63}a"; do
	printf 'hostname=%s\nmdns=false\n' "$invalid" > "$TMP/config"
	apply "$TMP/config"
	check_runtime rg34xxsp false
done
for invalid in '' 'False' '0' 'yes' 'false true'; do
	printf 'hostname=9\nmdns=%s\n' "$invalid" > "$TMP/config"
	apply "$TMP/config"
	check_runtime 9 true
done

# Unknown keys and shell-looking values are inert. An invalid later duplicate
# restores that key's default, without changing the other key.
cat > "$TMP/config" <<'EOF'
hostname=First
hostname=$(touch config-executed)
mdns=false
mdns=false; touch config-executed
anything=`touch config-executed`
EOF
(cd "$TMP"; apply "$TMP/config")
check_runtime rg34xxsp true
[ ! -e "$TMP/config-executed" ] || fail "card contents were executed"
printf 'hostname=First\nhostname=Last\nmdns=false\n' > "$TMP/config"
apply "$TMP/config"
check_runtime Last false

# Execute rcS's real config/update block with only paths and OS commands
# redirected to fixtures. The mount stub insists on a read-only TF1 mount.
awk '/^# BaseOS settings always/{copy=1} /^# Dev image extras/{copy=0} copy' "$RCS" \
	| sed -e "s|/dev/mmcblk0p7|$TMP/tf1|g" \
		-e "s|/mnt/sdcard|$TMP/sdcard|g" -e "s|/mnt/system|$TMP/system|g" \
		-e "s|/proc/mounts|$TMP/mounts|g" \
		-e "s|/usr/sbin/baseos-config|$SCRIPT|g" \
		-e "s|/usr/sbin/baseos-update|$TMP/bin/baseos-update|g" \
		-e "s|/bin/mount |$TMP/bin/mount |g" \
		-e "s|/bin/umount |$TMP/bin/umount |g" > "$TMP/boot-block"
# rcS lets the TF1 unmount finish in the background; wait for it here so the
# recorded order is complete before it is compared.
echo wait >> "$TMP/boot-block"
[ -s "$TMP/boot-block" ] || fail "rcS config block not found"
cat > "$TMP/bin/mount" <<'EOF'
#!/bin/sh
printf 'mount %s\n' "$*" >> "$BASEOS_TEST_LOG"
[ ! -e "$BASEOS_TEST_TMP/mount-fails" ] || exit 1
[ "$#" -eq 6 ] && [ "$1 $2 $3 $4 $5" = "-t vfat -o ro $BASEOS_TEST_TMP/tf1" ] || exit 2
printf '%s %s vfat ro 0 0\n' "$5" "$6" >> "$BASEOS_TEST_TMP/mounts"
if [ -f "$BASEOS_TEST_TMP/tf1-data/baseos.conf" ]; then
	cp "$BASEOS_TEST_TMP/tf1-data/baseos.conf" "$6/baseos.conf"
fi
EOF
cat > "$TMP/bin/umount" <<'EOF'
#!/bin/sh
printf 'umount %s\n' "$*" >> "$BASEOS_TEST_LOG"
awk -v mnt="$1" '$2 != mnt' "$BASEOS_TEST_TMP/mounts" > "$BASEOS_TEST_TMP/mounts.new"
mv "$BASEOS_TEST_TMP/mounts.new" "$BASEOS_TEST_TMP/mounts"
EOF
cat > "$TMP/bin/baseos-update" <<'EOF'
#!/bin/sh
printf 'update %s\n' "$*" >> "$BASEOS_TEST_LOG"
exit "${BASEOS_TEST_UPDATE_EXIT:-0}"
EOF
chmod 755 "$TMP/bin/mount" "$TMP/bin/umount" "$TMP/bin/baseos-update"
boot() { reset_runtime; USB_STORAGE_MODE="$1" sh "$TMP/boot-block"; }

# One-card boot reuses the frontend mount and leaves it mounted.
printf '%s/tf1 %s/sdcard vfat rw 0 0\n' "$TMP" "$TMP" > "$TMP/mounts"
printf 'hostname=TF1\nmdns=false\n' > "$TMP/sdcard/baseos.conf"
: > "$TMP/sdcard/test.bosupd"
boot 0
check_runtime TF1 false
[ "$(cat "$TMP/events")" = "$(printf 'hostname TF1\nupdate apply')" ] || fail "TF1 mount reuse"

# Steady-state boot with no .bosupd payload skips baseos-update apply.
rm -f "$TMP/sdcard/test.bosupd"
boot 0
check_runtime TF1 false
[ "$(cat "$TMP/events")" = 'hostname TF1' ] || fail "steady-state update skipped"

# With TF2 as frontend, TF1 alone supplies settings. Its temporary mount spans
# both configuration and the update scan, then is released.
printf '%s/tf2 %s/sdcard vfat rw 0 0\n' "$TMP" "$TMP" > "$TMP/mounts"
printf 'hostname=TF2\n' > "$TMP/sdcard/baseos.conf"
printf 'hostname=TF1\nmdns=false\n' > "$TMP/tf1-data/baseos.conf"
: > "$TMP/system/test.bosupd"
boot 0
check_runtime TF1 false
printf 'mount -t vfat -o ro %s/tf1 %s/system\nhostname TF1\nupdate apply\numount %s/system\n' \
	"$TMP" "$TMP" "$TMP" > "$TMP/want"
cmp -s "$TMP/want" "$TMP/events" || fail "TF2 boot mount/config/update ordering"
rm -f "$TMP/system/test.bosupd"

# Absent configuration and failed TF1 mounts do not borrow TF2 settings.
rm "$TMP/tf1-data/baseos.conf" "$TMP/system/baseos.conf"
boot 0
check_runtime rg34xxsp true
: > "$TMP/mount-fails"
boot 0
check_runtime rg34xxsp true
! grep -q '^umount ' "$TMP/events" || fail "unmounted a failed mount"
rm "$TMP/mount-fails"

# USB mode reads no card and invokes neither mount nor update, for either card.
for card in tf1 tf2; do
	printf '%s/%s %s/sdcard vfat rw 0 0\n' "$TMP" "$card" "$TMP" > "$TMP/mounts"
	boot 1
	check_runtime rg34xxsp true
	[ "$(cat "$TMP/events")" = 'hostname rg34xxsp' ] || fail "USB boot accessed storage"
done

# Configuration does not depend on the updater being available/successful.
printf '%s/tf2 %s/sdcard vfat rw 0 0\n' "$TMP" "$TMP" > "$TMP/mounts"
chmod 644 "$TMP/bin/baseos-update"
boot 0
check_runtime rg34xxsp true
grep -q '^umount ' "$TMP/events" || fail "mount leaked with missing updater"
chmod 755 "$TMP/bin/baseos-update"
BASEOS_TEST_UPDATE_EXIT=1 boot 0
check_runtime rg34xxsp true
grep -q '^umount ' "$TMP/events" || fail "mount leaked after update failure"

# Exercise the actual update scan and cleanup too. Only its block-device check
# is adapted to a regular fixture, allowing a privilege-free fallback mount.
# Keep the shared logger's writes inside the same fixture on the host.
mkdir -p "$TMP/data"
sed -e "s|/data/|$TMP/data/|g" \
	-e "s|/tmp/baseos-boot.log|$TMP/baseos-boot.log|g" \
	-e "s|/mnt/sdcard|$TMP/sdcard|g" \
	-e "s|/run/usb-storage-device|$TMP/usb-storage-device|g" \
	"$HERE/overlay/usr/share/baseos/boot-log.sh" > "$TMP/boot-log.sh"
sed '/^# One-time migration/,$d' "$HERE/overlay/usr/sbin/baseos-update" \
	| sed -e "s|/dev/mmcblk0p7|$TMP/tf1|g" \
		-e "s|/usr/share/baseos/boot-log.sh|$TMP/boot-log.sh|g" \
		-e "s|/mnt/sdcard|$TMP/sdcard|g" -e "s|/tmp/.baseos-own|$TMP/own|g" \
		-e "s|/proc/mounts|$TMP/mounts|g" \
		-e "s|/run/usb-storage-device|$TMP/usb-storage-device|g" \
		-e 's/\[ -b "\$OWN_FAT" \]/[ -f "$OWN_FAT" ]/' > "$TMP/update-scan"
cat >> "$TMP/update-scan" <<'EOF'
scan_dirs
printf '%s\n' "$DIRS" > "$BASEOS_TEST_TMP/dirs"
cleanup
EOF
: > "$TMP/tf1"
printf '%s/tf1 %s/sdcard vfat rw 0 0\n' "$TMP" "$TMP" > "$TMP/mounts"
reset_runtime
sh "$TMP/update-scan"
[ "$(cat "$TMP/dirs")" = "$TMP/sdcard" ] || fail "update repeated TF1 frontend directory"
[ ! -s "$TMP/events" ] || fail "update remounted TF1 frontend"
printf '%s/tf1 %s/system vfat ro 0 0\n' "$TMP" "$TMP" > "$TMP/mounts"
sh "$TMP/update-scan"
[ "$(cat "$TMP/dirs")" = "$TMP/sdcard $TMP/system" ] || fail "update missed config mount"
[ ! -s "$TMP/events" ] || fail "update changed a mount it did not own"
: > "$TMP/mounts"
sh "$TMP/update-scan"
[ "$(cat "$TMP/dirs")" = "$TMP/sdcard $TMP/own" ] || fail "standalone update fallback"
printf 'mount -t vfat -o ro %s/tf1 %s/own\numount %s/own\n' "$TMP" "$TMP" "$TMP" > "$TMP/want"
cmp -s "$TMP/want" "$TMP/events" || fail "standalone update mount cleanup"
: > "$TMP/usb-storage-device"
reset_runtime
sh "$TMP/update-scan"
[ ! -s "$TMP/events" ] || fail "standalone update touched USB-exported storage"
[ -z "$(cat "$TMP/dirs")" ] || fail "standalone update scanned USB-exported storage"

# Keep the charger and USB decisions before settings, and services after them.
line_of() { grep -nF "$1" "$RCS" | head -1 | cut -d: -f1; }
charger="$(line_of '/usr/sbin/baseos-charger')"
storage="$(line_of '/usr/sbin/usb-storage-mode prepare')"
config="$(line_of '/usr/sbin/baseos-config "$BASEOS_CONFIG"')"
audio="$(line_of '/usr/sbin/h700-speaker-amp-load >')"
services="$(line_of '/etc/init.d/dev')"
[ "$config" -lt "$audio" ] && [ "$audio" -lt "$services" ] || fail "audio configuration order"
[ "$charger" -lt "$storage" ] && [ "$storage" -lt "$config" ] && [ "$config" -lt "$services" ] \
	|| fail "boot ordering"
[ "$(grep -c '/usr/sbin/baseos-config "$BASEOS_CONFIG"' "$RCS")" -eq 1 ] || fail "multiple config applications"
! grep -Eq 'rename-hostname|rename_hostname|RENAMED|/bin/hostname|avahi-daemon' "$RCS" \
	|| fail "obsolete hostname or mDNS boot path"

echo "baseos-config tests passed"
