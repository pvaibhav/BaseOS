#!/bin/sh
# Every released updater (1.0.0 to 1.4.0) must apply a 1.5.1-shaped payload:
# manifest with a native-generation line, rootfs.img.gz, and the payload's own
# updater as a third member. The per-card 1.5.0 updater refuses it (different
# generation); the updater carried in the payload then applies it by hand.
# Stubs and a small file stand in for the card, as in test-update-apply.sh.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"
OLD="$(mktemp -d)"
trap 'rm -rf "$OLD"' EXIT
# b77c0be is the source of the per-card 1.5.0 root filesystems.
for version in v1.0.0 v1.1.0 v1.2.0 v1.2.1 v1.3.0 v1.4.0 b77c0be; do
  mkdir -p "$OLD/$version"
  git -C "$HERE" show "$version:overlay/usr/sbin/baseos-update" > "$OLD/$version/baseos-update"
  git -C "$HERE" show "$version:overlay/usr/share/baseos/boot-log.sh" > "$OLD/$version/boot-log.sh" 2>/dev/null \
    || : > "$OLD/$version/boot-log.sh"
done

docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE":/repo:ro -v "$OLD":/old:ro alpine:3.20 sh -euc '
  SLOT_BYTES=8388608 SLOT_SECTORS=16384
  mkdir -p /mnt/sdcard /data /dev /usr/sbin /usr/share/baseos /usr/local/bin
  stub() { printf "%s\n" "#!/bin/sh" "$2" > "$1"; chmod 755 "$1"; }
  stub /usr/bin/baseos-splash "exit 0"
  stub /usr/local/bin/reboot "echo reboot >> /tmp/reboot.log"
  stub /usr/local/bin/sleep "exit 0"
  stub /usr/local/bin/mountpoint "exit 0"
  stub /usr/sbin/native-health "exit 0"
  cat > /usr/sbin/gptslot <<"EOF"
#!/bin/sh
case "$2" in
  geometry) printf "%s\n" SLOT_ACTIVE=A SLOT_BASE=2048 SLOT_SECTORS=16384 SLOT_ACTIVE_START=2048 SLOT_INACTIVE_START=18432 ;;
  flip) echo flip >> /tmp/flip.log ;;
  *) exit 2 ;;
esac
EOF
  chmod 755 /usr/sbin/gptslot

  # The 1.5.1 payload shape, exactly as tools/mkupdate.py writes it.
  rm -rf /tmp/pay; mkdir -p /tmp/pay; cd /tmp/pay
  dd if=/dev/urandom of=slot.img bs=1M count=8 status=none
  printf "BASEOS_VERSION=1.5.1" | dd of=slot.img bs=1 seek=0 conv=notrunc status=none
  sha="$(sha256sum slot.img | cut -d" " -f1)"
  gzip -c slot.img > rootfs.img.gz
  printf "format=baseos-update/1\ntarget=rg40xxv\nversion=1.5.1\nbuild=test\nslot-sectors=%s\nimage-size=%s\nimage-sha256=%s\nnative-generation=%s\n" \
    "$SLOT_SECTORS" "$SLOT_BYTES" "$sha" 89abcdef0123456789abcdef0123456789abcdef0123456789abcdef01234567 > manifest
  cp /repo/overlay/usr/sbin/baseos-update baseos-update
  tar -cf /tmp/payload.bosupd manifest rootfs.img.gz baseos-update
  cd /

  fresh() {
    rm -rf /data/update /etc/baseos /tmp/flip.log /tmp/reboot.log /mnt/sdcard/* /tmp/native-fdt
    : > /dev/mmcblk0; truncate -s 32M /dev/mmcblk0
    printf "BASEOS=1\nBASEOS_VERSION=%s\nBASEOS_BUILD=%s\nBASEOS_TARGET=rg40xxv\n" "$1" "$1" > /etc/baseos-release
    cp /tmp/payload.bosupd /mnt/sdcard/baseos-rg40xxv-1.5.1.bosupd
  }
  written() {
    test -f /tmp/flip.log && test -f /tmp/reboot.log
    [ "$(dd if=/dev/mmcblk0 bs=1M skip=9 count=8 2>/dev/null | sha256sum | cut -d" " -f1)" = "$sha" ]
  }

  for version in v1.0.0 v1.1.0 v1.2.0 v1.2.1 v1.3.0 v1.4.0; do
    fresh "${version#v}"
    cp "/old/$version/baseos-update" /usr/sbin/baseos-update; chmod 755 /usr/sbin/baseos-update
    cp "/old/$version/boot-log.sh" /usr/share/baseos/boot-log.sh
    PATH=/usr/local/bin:$PATH /usr/sbin/baseos-update apply > /tmp/out.log 2>&1 || { cat /tmp/out.log; exit 1; }
    written || { echo "FAIL: $version did not write and flip"; cat /tmp/out.log; exit 1; }
    echo "ok: $version updater applies the 1.5.1 payload"
  done

  # Per-card 1.5.0: its own updater refuses; the carried updater applies it.
  fresh 1.5.0
  mkdir -p /etc/baseos /tmp/native-fdt
  printf "%s" "0123456789abcdef0123456789abcdef" > /etc/baseos/native-generation
  printf "boe\n" > /etc/baseos/native-panel
  cp /etc/baseos/native-generation /tmp/native-fdt/baseos,native-boot
  sed "s|/sys/firmware/devicetree/base/chosen/baseos,native-boot|/tmp/native-fdt/baseos,native-boot|" \
    /old/b77c0be/baseos-update > /usr/sbin/baseos-update
  chmod 755 /usr/sbin/baseos-update
  cp /old/b77c0be/boot-log.sh /usr/share/baseos/boot-log.sh
  if PATH=/usr/local/bin:$PATH /usr/sbin/baseos-update apply > /tmp/out.log 2>&1; then :; fi
  test ! -f /tmp/flip.log || { echo "FAIL: the 1.5.0 updater applied a payload of another generation"; exit 1; }
  grep -q "payload does not match the installed native boot generation" /mnt/sdcard/baseos-boot.log /data/baseos-boot.log /tmp/*.log 2>/dev/null \
    || { echo "FAIL: 1.5.0 refusal reason not logged"; cat /tmp/out.log; exit 1; }
  echo "ok: per-card 1.5.0 updater refuses the 1.5.1 payload"
  tar -xOf /mnt/sdcard/baseos-rg40xxv-1.5.1.bosupd baseos-update > /tmp/baseos-update-1.5.1
  sed -i "s|/sys/firmware/devicetree/base/chosen/baseos,native-boot|/tmp/native-fdt/baseos,native-boot|" /tmp/baseos-update-1.5.1
  PATH=/usr/local/bin:$PATH sh /tmp/baseos-update-1.5.1 apply > /tmp/out.log 2>&1 || { cat /tmp/out.log; exit 1; }
  written || { echo "FAIL: the carried updater did not write and flip"; cat /tmp/out.log; exit 1; }
  echo "ok: the updater carried in the payload applies it on a 1.5.0 card"
  echo "RESULT: PASS"
'
