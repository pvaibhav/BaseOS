#!/bin/sh
# Regression test for baseos-update: what it accepts, what it refuses, and —
# above all — that it never flips the GPT unless the inactive slot verified.
#
# Runs entirely on stubs and a small regular file standing in for the card, so
# it needs no device and no privileges. The BusyBox tar/gunzip/sha256sum used
# here are the same applets the handheld runs.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"

docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE":/repo:ro \
  -v "$HERE/overlay/usr/sbin/baseos-update":/baseos-update-source:ro \
  -v "$HERE/overlay/usr/share/baseos/boot-log.sh":/usr/share/baseos/boot-log.sh:ro \
  alpine:3.20 sh -euc '
  apk add -q python3 e2fsprogs-extra
  python3 /repo/tests/test-mkupdate.py
  # Docker masks sys/firmware. Rebind only the FDT file literal in this private
  # CLI copy; production source and all update logic remain unchanged.
  sed "s|/sys/firmware/devicetree/base/chosen/baseos,native-boot|/tmp/native-fdt/baseos,native-boot|" \
    /baseos-update-source > /usr/sbin/baseos-update
  chmod 755 /usr/sbin/baseos-update
  # 8 MiB slots: small enough to stay quick, large enough that the chunked
  # write and read-back actually loop (step = max(1, MiB/16) = 1 MiB here).
  SLOT_BYTES=8388608
  SLOT_SECTORS=16384
  INACTIVE_MB=9               # SLOT_INACTIVE_START 18432 / 2048

  mkdir -p /mnt/sdcard /data /dev
  : > /dev/mmcblk0
  truncate -s 32M /dev/mmcblk0

  cat > /etc/baseos-release <<EOF
BASEOS=1
BASEOS_VERSION=1.0.0
BASEOS_BUILD=v1.0.0
BASEOS_TARGET=rg40xxv
EOF

  stub() { printf "%s\n" "#!/bin/sh" "$2" > "$1"; chmod 755 "$1"; }
  stub /usr/bin/baseos-splash "printf \"%s\\n\" \"\$*\" >> /tmp/splash.log"
  stub /usr/local/bin/reboot "echo reboot >> /tmp/reboot.log"
  stub /usr/local/bin/sleep "exit 0"
  stub /usr/local/bin/mountpoint "echo probe >> /tmp/card-probes; [ ! -e /tmp/no-card ]"
  mkdir -p /usr/sbin
  cat > /usr/sbin/gptslot <<"EOF"
#!/bin/sh
[ -f /tmp/no-layout ] && exit 2
case "$2" in
  geometry)
    echo "SLOT_ACTIVE=A"
    echo "SLOT_BASE=2048"
    echo "SLOT_SECTORS=16384"
    echo "SLOT_ACTIVE_START=2048"
    echo "SLOT_INACTIVE_START=18432"
    ;;
  flip) [ ! -f /tmp/fail-flip ] || exit 1; echo flip >> /tmp/flip.log ;;
  *) exit 2 ;;
esac
EOF
  chmod 755 /usr/sbin/gptslot

  # Build a payload named after its version, so the on-card glob order matches
  # what a user actually accumulates. $1 target, $2 slot-sectors, $3
  # clean|corrupt, $4 version (default 1.0.1), $5 "keep" to leave existing
  # payloads in place.
  make_payload() {
    ver="${4:-1.0.1}"
    rm -rf /tmp/pay; mkdir -p /tmp/pay; cd /tmp/pay
    dd if=/dev/urandom of=slot.img bs=1M count=8 status=none
    printf "%s" "$ver" | dd of=slot.img bs=1 seek=0 conv=notrunc status=none
    sha="$(sha256sum slot.img | cut -d" " -f1)"
    if [ "$3" = corrupt ]; then
      dd if=/dev/urandom of=slot.img bs=512 count=1 conv=notrunc status=none
    fi
    gzip -c slot.img > rootfs.img.gz
    printf "format=baseos-update/1\ntarget=%s\nversion=%s\nbuild=test\n" "$1" "$ver" > manifest
    printf "slot-sectors=%s\nimage-size=%s\nimage-sha256=%s\n" \
      "$2" "$SLOT_BYTES" "$sha" >> manifest
    [ "${5:-}" = keep ] || rm -f /mnt/sdcard/*.bosupd
    tar -cf "/mnt/sdcard/baseos-rg40xxv-$ver.bosupd" manifest rootfs.img.gz
    cd /
  }

  reset() {
    rm -f /tmp/flip.log /tmp/splash.log /tmp/reboot.log /tmp/card-probes /tmp/no-card \
      /mnt/sdcard/baseos-boot.log /data/baseos-boot.log /tmp/baseos-boot.log
    rm -rf /data/update
    rm -rf /etc/baseos /tmp/native-fdt
    rm -f /usr/sbin/native-health
  }
  no_flip() {
    [ -f /tmp/flip.log ] && { echo "FAIL: $1 — the GPT was flipped" >&2; exit 1; }
    return 0
  }

  native_policy() {
    mkdir -p /etc/baseos
    printf "%s" "0123456789abcdef0123456789abcdef" > /etc/baseos/native-generation
    stub /usr/sbin/native-health "exit 0"
    NATIVE_HEX="$(od -An -v -tx1 /etc/baseos/native-generation | tr -d " \n")"
  }
  native_field() {
    printf "native-generation=%s\n" "$1" >> /tmp/pay/manifest
    tar -cf /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd -C /tmp/pay manifest rootfs.img.gz
  }
  native_fdt() {
    mkdir -p /tmp/native-fdt
    cp /etc/baseos/native-generation /tmp/native-fdt/baseos,native-boot
  }
  refuse_unchanged() {
    before="$(sha256sum /dev/mmcblk0)"
    if baseos-update apply; then echo "FAIL: $1 accepted" >&2; exit 1; fi
    no_flip "$1"
    [ "$(sha256sum /dev/mmcblk0)" = "$before" ] || { echo "FAIL: $1 wrote the card" >&2; exit 1; }
    test ! -f /tmp/reboot.log
  }

  echo "== a native payload updates a stock card; a native card needs one =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  native_field 0000000000000000000000000000000000000000000000000000000000000000
  refuse_unchanged "zero native payload on legacy card"
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  native_field 89abcdef0123456789abcdef0123456789abcdef0123456789abcdef01234567
  baseos-update apply
  test -f /tmp/flip.log
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy
  refuse_unchanged "legacy payload on native card"

  echo "== the same or another model generation applies with FDT or stock fallback =="
  for running in native stock other; do
    reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy
    if [ "$running" = other ]; then native_field 89abcdef0123456789abcdef0123456789abcdef0123456789abcdef01234567
    else native_field "$NATIVE_HEX"; fi
    [ "$running" = stock ] || native_fdt
    prefix="$(dd if=/dev/mmcblk0 bs=1M count=9 2>/dev/null | sha256sum)"
    suffix="$(dd if=/dev/mmcblk0 bs=1M skip=17 count=15 2>/dev/null | sha256sum)"
    baseos-update apply
    test -f /tmp/flip.log
    [ "$(dd if=/dev/mmcblk0 bs=1M count=9 2>/dev/null | sha256sum)" = "$prefix" ]
    [ "$(dd if=/dev/mmcblk0 bs=1M skip=17 count=15 2>/dev/null | sha256sum)" = "$suffix" ]
  done

  echo "== zero or malformed native generations never write the card =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy
  native_field 0000000000000000000000000000000000000000000000000000000000000000
  refuse_unchanged "zero payload generation"
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_field "$NATIVE_HEX"; native_fdt
  printf "%s" "fedcba9876543210fedcba9876543210" > /tmp/native-fdt/baseos,native-boot
  refuse_unchanged "wrong running generation"
  for value in "" short 000000000000000000000000000000000000000000000000000000000000000G; do
    reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_field "$value"
    refuse_unchanged "malformed manifest generation"
  done
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy
  printf "native-generation=%s\n" "$NATIVE_HEX" >> /tmp/pay/manifest
  native_field "$NATIVE_HEX"
  refuse_unchanged "duplicate manifest generation"
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_field "$NATIVE_HEX"
  printf short > /etc/baseos/native-generation
  refuse_unchanged "short installed generation"
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_field "$NATIVE_HEX"
  mv /etc/baseos/native-generation /etc/baseos/actual-generation
  ln -s actual-generation /etc/baseos/native-generation
  refuse_unchanged "symlink installed generation"
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_field "$NATIVE_HEX"
  mv /etc/baseos /etc/actual-baseos
  ln -s actual-baseos /etc/baseos
  refuse_unchanged "symlink installed policy directory"
  rm -rf /etc/actual-baseos
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_field "$NATIVE_HEX"
  rm /usr/sbin/native-health
  refuse_unchanged "missing installed native helper"
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_field "$NATIVE_HEX"; native_fdt
  printf short > /tmp/native-fdt/baseos,native-boot
  refuse_unchanged "malformed native FDT"
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_field "$NATIVE_HEX"; native_fdt
  mv /tmp/native-fdt/baseos,native-boot /tmp/native-fdt/actual-generation
  ln -s actual-generation /tmp/native-fdt/baseos,native-boot
  refuse_unchanged "symlink native FDT property"
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean; native_policy; native_fdt
  rm /etc/baseos/native-generation
  refuse_unchanged "native FDT with missing installed policy"
  rm -f /mnt/sdcard/*.bosupd

  echo "== no payload is a silent no-op =="
  reset
  baseos-update apply
  no_flip "empty card"
  test ! -f /tmp/splash.log || { echo "FAIL: painted a pill with no payload" >&2; exit 1; }
  echo "ok"

  echo "== a payload for another target is refused =="
  reset; make_payload rg35xxh "$SLOT_SECTORS" clean
  if baseos-update apply; then echo "FAIL: wrong target accepted" >&2; exit 1; fi
  no_flip "wrong target"
  test -f /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd
  echo "ok"

  echo "== a payload for a different slot size is refused =="
  reset; make_payload rg40xxv 4096 clean
  if baseos-update apply; then echo "FAIL: wrong slot size accepted" >&2; exit 1; fi
  no_flip "wrong slot size"
  echo "ok"

  echo "== a corrupted image is refused after the write =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" corrupt
  if baseos-update apply; then echo "FAIL: corrupt image accepted" >&2; exit 1; fi
  no_flip "hash mismatch"
  test -f /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd
  grep -q "UPDATE FAILED" /tmp/splash.log || { echo "FAIL: no failure pill" >&2; exit 1; }
  echo "ok: written but never committed"

  echo "== a card with no A/B layout is left alone =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  : > /tmp/no-layout
  baseos-update apply
  rm -f /tmp/no-layout
  no_flip "pre-1.0 layout"
  echo "ok"

  echo "== a failed slot flip retains the payload =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  : > /tmp/fail-flip
  if baseos-update apply; then echo "FAIL: failed flip accepted" >&2; exit 1; fi
  rm -f /tmp/fail-flip
  no_flip "failed commit"
  test -f /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd
  test ! -f /tmp/reboot.log
  echo "ok"

  echo "== a good payload is written, verified and committed =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  want="$(sha256sum /tmp/pay/slot.img | cut -d" " -f1)"
  baseos-update apply
  got="$(dd if=/dev/mmcblk0 bs=1M skip=$INACTIVE_MB count=8 status=none | sha256sum | cut -d" " -f1)"
  test "$got" = "$want" || { echo "FAIL: inactive slot content" >&2; exit 1; }
  test -f /tmp/flip.log || { echo "FAIL: no commit" >&2; exit 1; }
  test -f /tmp/reboot.log || { echo "FAIL: no reboot" >&2; exit 1; }
  test ! -e /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd || { echo "FAIL: applied payload remains" >&2; exit 1; }
  grep -q "UPDATING SYSTEM" /tmp/splash.log || { echo "FAIL: no progress pill" >&2; exit 1; }
  # The bar must actually move, and never backwards: one paint before the write,
  # one per written chunk, one per verified chunk, one at the commit.
  awk "/UPDATING SYSTEM/ {print \$2}" /tmp/splash.log > /tmp/progress.txt
  test "$(wc -l < /tmp/progress.txt)" -ge 10 \
    || { echo "FAIL: progress pill did not move (only $(wc -l < /tmp/progress.txt) paints)" >&2; exit 1; }
  awk "NR > 1 && \$1 < prev { exit 1 } { prev = \$1 }" /tmp/progress.txt \
    || { echo "FAIL: progress went backwards" >&2; exit 1; }
  test "$(head -1 /tmp/progress.txt)" -eq 0 || { echo "FAIL: bar should start at 0" >&2; exit 1; }
  test "$(tail -1 /tmp/progress.txt)" -eq 98 || { echo "FAIL: bar should reach 98" >&2; exit 1; }
  echo "  progress: $(tr "\n" " " < /tmp/progress.txt)"
  grep -qx "trial=1.0.1" /data/update/state || { echo "FAIL: no trial state" >&2; exit 1; }
  grep -q "update: 1.0.1 written and checked; restarting into it" /mnt/sdcard/baseos-boot.log \
    || { echo "FAIL: update events missing from shared card log" >&2; exit 1; }
  ! grep -q -e "records out" -e "records in" -e " copied, " /mnt/sdcard/baseos-boot.log \
    || { echo "FAIL: raw dd output in the card log" >&2; exit 1; }
  test "$(grep -c "update: " /mnt/sdcard/baseos-boot.log)" -eq 2 \
    || { echo "FAIL: an update should take two log lines" >&2; cat /mnt/sdcard/baseos-boot.log >&2; exit 1; }
  echo "ok"

  grep -q "^$want 1.0.1 test$" /data/update/history \
    || { echo "FAIL: the commit was not recorded before the reboot" >&2; exit 1; }
  echo "ok"

  echo "== the trial ends when a frontend session starts =="
  baseos-update confirm
  test ! -f /data/update/state || { echo "FAIL: trial state survived confirm" >&2; exit 1; }
  grep -q "update: .*confirmed" /mnt/sdcard/baseos-boot.log || { echo "FAIL: nothing logged" >&2; exit 1; }
  test ! -e /data/update/log && test ! -e /tmp/baseos-update.log \
    || { echo "FAIL: legacy update logs were recreated" >&2; exit 1; }
  echo "ok"

  echo "== a deleted payload leaves the next boot with no update work =="
  rm -f /tmp/flip.log /tmp/splash.log
  baseos-update apply
  no_flip "empty card after update"
  test ! -f /tmp/splash.log

  echo "== an already-applied payload copied back is skipped =="
  tar -cf /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd -C /tmp/pay manifest rootfs.img.gz
  rm -f /tmp/flip.log
  baseos-update apply
  no_flip "re-applying the same image"
  echo "ok: history still prevents reapplication"

  echo "== TF1 read-only scan mount is restored after cleanup =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  mkdir -p /mnt/system
  mv /mnt/sdcard/*.bosupd "/mnt/system/update with spaces.bosupd"
  # Supply mount metadata without requiring privileged mounts in the test.
  cat > /usr/local/bin/awk <<"EOF"
#!/bin/sh
case "$*" in
  */proc/mounts) printf "%s\n" "/dev/mmcblk0p7 /mnt/system vfat ro,relatime 0 0" | /usr/bin/awk "$1" "$2" "$3" ;;
  *) exec /usr/bin/awk "$@" ;;
esac
EOF
  cat > /usr/local/bin/mount <<"EOF"
#!/bin/sh
printf "%s\n" "$*" >> /tmp/remount.log
[ ! -f /tmp/fail-remount ]
EOF
  chmod 755 /usr/local/bin/awk /usr/local/bin/mount
  baseos-update apply
  test ! -e "/mnt/system/update with spaces.bosupd"
  test "$(cat /tmp/remount.log)" = "-o remount,rw /mnt/system
-o remount,ro /mnt/system"
  test -f /tmp/reboot.log
  echo "ok"

  echo "== failed writable remount keeps the payload and still reboots =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  mv /mnt/sdcard/*.bosupd /mnt/system/update.bosupd
  : > /tmp/fail-remount
  baseos-update apply
  test -f /mnt/system/update.bosupd
  test -f /tmp/reboot.log
  grep -q "keeping /mnt/system/update.bosupd" /mnt/sdcard/baseos-boot.log
  rm -f /usr/local/bin/awk /usr/local/bin/mount /tmp/fail-remount /mnt/system/update.bosupd
  echo "ok"

  echo "== failed deletion keeps the committed update successful =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  cat > /usr/local/bin/rm <<"EOF"
#!/bin/sh
case "$*" in
  *.bosupd) exit 1 ;;
  *) exec /bin/rm "$@" ;;
esac
EOF
  chmod 755 /usr/local/bin/rm
  baseos-update apply
  /bin/rm /usr/local/bin/rm
  test -f /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd
  test -f /tmp/reboot.log
  grep -q "could not remove applied payload" /mnt/sdcard/baseos-boot.log
  echo "ok"

  echo "== a payload matching the running build is skipped =="
  # A payload whose version and build match the running system is skipped
  # outright, so the file left on the card is inert from the first boot after an
  # update — before anything has been confirmed.
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  cat > /etc/baseos-release <<EOF
BASEOS=1
BASEOS_VERSION=1.0.1
BASEOS_BUILD=test
BASEOS_TARGET=rg40xxv
EOF
  baseos-update apply
  no_flip "payload equals the running build"
  echo "ok: no reboot loop on the first boot after an update"

  echo "== a stale payload does not mask a newer one beside it =="
  # Exactly the field report: 1.0.1 and 1.0.2 both on the card. 1.0.1 sorts
  # first and is the running build, so a scan that stops at the first file
  # finds nothing to do and never looks at 1.0.2.
  reset
  cat > /etc/baseos-release <<EOF
BASEOS=1
BASEOS_VERSION=1.0.1
BASEOS_BUILD=test
BASEOS_TARGET=rg40xxv
EOF
  make_payload rg40xxv "$SLOT_SECTORS" clean 1.0.1
  make_payload rg40xxv "$SLOT_SECTORS" clean 1.0.2 keep
  newer="$(sha256sum /tmp/pay/slot.img | cut -d" " -f1)"
  test "$(ls /mnt/sdcard/*.bosupd | wc -l)" -eq 2 || { echo "FAIL: setup" >&2; exit 1; }
  baseos-update apply
  test -f /tmp/flip.log || { echo "FAIL: the newer payload was never found" >&2; exit 1; }
  got="$(dd if=/dev/mmcblk0 bs=1M skip=$INACTIVE_MB count=8 status=none | sha256sum | cut -d" " -f1)"
  test "$got" = "$newer" || { echo "FAIL: applied the wrong payload" >&2; exit 1; }
  grep -qx "trial=1.0.2" /data/update/state || { echo "FAIL: wrong version applied" >&2; exit 1; }
  test -f /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd
  test ! -e /mnt/sdcard/baseos-rg40xxv-1.0.2.bosupd
  echo "ok: only the applied payload is deleted"

  echo "== an older payload is never applied =="
  # Without this the stale payload becomes applicable again the moment you
  # update past it: downgrade, then re-apply the newer one, forever.
  reset
  cat > /etc/baseos-release <<EOF
BASEOS=1
BASEOS_VERSION=1.0.2
BASEOS_BUILD=test
BASEOS_TARGET=rg40xxv
EOF
  make_payload rg40xxv "$SLOT_SECTORS" clean 1.0.1
  baseos-update apply
  no_flip "older payload"
  echo "ok: no downgrade ping-pong"

  echo "== a rolled-back payload is not applied again =="
  # The failure that oscillates forever if the commit is only recorded on
  # success: apply -> flip -> three bad boots -> roll back -> and the payload is
  # still sitting on the card, so the next boot re-applies the same bad update.
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  cat > /etc/baseos-release <<EOF
BASEOS=1
BASEOS_VERSION=1.0.0
BASEOS_BUILD=v1.0.0
BASEOS_TARGET=rg40xxv
EOF
  baseos-update apply                          # commits and reboots
  test -f /tmp/flip.log || { echo "FAIL: no commit" >&2; exit 1; }
  baseos-update boot-check                     # boot 1 of the trial
  baseos-update boot-check                     # boot 2
  baseos-update boot-check                     # boot 3: restore the old slot
  test "$(wc -l < /tmp/flip.log)" -eq 2 || { echo "FAIL: no rollback flip" >&2; exit 1; }
  tar -cf /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd -C /tmp/pay manifest rootfs.img.gz
  baseos-update apply                          # back on the old slot, payload copied back
  test "$(wc -l < /tmp/flip.log)" -eq 2 \
    || { echo "FAIL: the rolled-back payload was applied again" >&2; exit 1; }
  echo "ok: a bad update cannot oscillate"

  echo "== a slot that never reaches a session is restored =="
  reset
  mkdir -p /data/update
  printf "trial=1.0.1\nattempts=1\nrestored-from=A\nsha=deadbeef\n" > /data/update/state
  baseos-update boot-check          # attempt 2 of 3: still on trial
  test ! -f /tmp/flip.log || { echo "FAIL: rolled back too early" >&2; exit 1; }
  grep -qx "attempts=2" /data/update/state || { echo "FAIL: attempts not counted" >&2; exit 1; }
  baseos-update boot-check          # attempt 3 of 3: restore the old slot
  test -f /tmp/flip.log || { echo "FAIL: no rollback flip" >&2; exit 1; }
  test ! -f /data/update/state || { echo "FAIL: trial state survived rollback" >&2; exit 1; }
  grep -q "RESTORING SYSTEM" /tmp/splash.log || { echo "FAIL: no restore pill" >&2; exit 1; }
  test ! -e /tmp/card-probes && test ! -e /mnt/sdcard/baseos-boot.log \
    || { echo "FAIL: pre-card boot check accessed the frontend card" >&2; exit 1; }
  grep -q "update: trying 1.0.1, start 2 of 3" /data/baseos-boot.log \
    || { echo "FAIL: trial diagnostics missing from persistent early log" >&2; exit 1; }
  grep -q "restoring" /data/baseos-boot.log \
    || { echo "FAIL: rollback diagnostics missing from persistent early log" >&2; exit 1; }
  echo "ok: rollback state and pre-card diagnostics survive without probing the card"

  echo "== confirmation cleans a payload left by an older updater =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  matched_sha="$sha"
  cp /etc/baseos-release /tmp/old-release
  sed -e "s/BASEOS_VERSION=.*/BASEOS_VERSION=1.0.1/" \
      -e "s/BASEOS_BUILD=.*/BASEOS_BUILD=test/" /tmp/old-release > /etc/baseos-release
  mkdir -p /data/update
  printf "trial=1.0.1\nbuild=test\nsha=%s\nattempts=1\n" "$matched_sha" > /data/update/state
  make_payload rg40xxv "$SLOT_SECTORS" clean 1.0.2 keep
  baseos-update confirm
  test ! -e /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd
  test -e /mnt/sdcard/baseos-rg40xxv-1.0.2.bosupd
  test ! -e /data/update/state
  no_flip "trial archive cleanup"
  echo "ok: exact applied archive removed; unrelated update retained"

  echo "== confirmation preserves a same-build payload with a different image hash =="
  reset; make_payload rg40xxv "$SLOT_SECTORS" clean
  mkdir -p /data/update
  printf "trial=1.0.1\nbuild=test\nsha=%s\nattempts=1\n" "$matched_sha" > /data/update/state
  baseos-update confirm
  test -e /mnt/sdcard/baseos-rg40xxv-1.0.1.bosupd
  echo "ok"
  mv /tmp/old-release /etc/baseos-release
  rm -f /mnt/sdcard/*.bosupd

  echo "== confirmation without a frontend card retains diagnostics on data =="
  reset
  : > /tmp/no-card
  mkdir -p /data/update
  printf "trial=1.0.1\nattempts=1\n" > /data/update/state
  baseos-update confirm
  test ! -f /data/update/state || { echo "FAIL: trial state survived confirm" >&2; exit 1; }
  grep -q "update: .*confirmed" /data/baseos-boot.log \
    || { echo "FAIL: confirmation diagnostics missing from persistent early log" >&2; exit 1; }
  test ! -e /mnt/sdcard/baseos-boot.log \
    || { echo "FAIL: log created on an unmounted frontend path" >&2; exit 1; }
  echo "ok"

  echo "== an unavailable card log falls back without blocking confirmation =="
  reset
  mkdir /mnt/sdcard/baseos-boot.log
  mkdir -p /data/update
  printf "trial=1.0.1\nattempts=1\n" > /data/update/state
  baseos-update confirm
  test ! -f /data/update/state || { echo "FAIL: logging blocked confirmation" >&2; exit 1; }
  grep -q "update: .*confirmed" /data/baseos-boot.log \
    || { echo "FAIL: unavailable card log did not fall back to data" >&2; exit 1; }
  rmdir /mnt/sdcard/baseos-boot.log
  echo "ok"

  echo "== an unavailable early log falls back without blocking a trial boot =="
  reset
  mkdir /data/baseos-boot.log
  mkdir -p /data/update
  printf "trial=1.0.1\nattempts=1\n" > /data/update/state
  baseos-update boot-check
  grep -qx "attempts=2" /data/update/state || { echo "FAIL: logging blocked trial count" >&2; exit 1; }
  grep -q "update: trying 1.0.1, start 2 of 3" /tmp/baseos-boot.log \
    || { echo "FAIL: unavailable early log did not fall back to RAM" >&2; exit 1; }
  test ! -e /tmp/card-probes || { echo "FAIL: fallback probed the frontend card" >&2; exit 1; }
  rmdir /data/baseos-boot.log
  echo "ok"

  echo "== USB storage confirmation logs in RAM without probing the card =="
  reset
  printf "/dev/mmcblk1\n" > /run/usb-storage-device
  mkdir -p /data/update
  printf "trial=1.0.1\nattempts=1\n" > /data/update/state
  baseos-update confirm
  test ! -f /data/update/state || { echo "FAIL: trial state survived confirm" >&2; exit 1; }
  grep -q "update: .*confirmed" /tmp/baseos-boot.log \
    || { echo "FAIL: USB storage confirmation missing from RAM log" >&2; exit 1; }
  test ! -e /tmp/card-probes && test ! -e /mnt/sdcard/baseos-boot.log \
    && test ! -e /data/baseos-boot.log \
    || { echo "FAIL: USB storage confirmation touched a persistent log" >&2; exit 1; }
  rm -f /run/usb-storage-device
  echo "ok"

  echo "RESULT: PASS — payload validation, verify-then-commit, and rollback"
'
