#!/bin/sh
# Compose one target's flashable BaseOS image from prepared StockMod inputs.
# Usage: ./build-image.sh <target> [--native-release DIR]
# --native-release takes the model's native boot files (from
# experiments/h700-boot-speed/native/build-release.py, OUT/release). The rootfs
# gets them in /usr/share/baseos/native plus /etc/baseos/native-generation, so
# build-update.sh emits a payload whose first boot installs them
# (baseos-boot-migrate), and the card image gets them already written: capsule
# at sector 16, recovery package at the package sector, firmware and kernel
# image at boot partition + 40/48 MiB. Nothing in it belongs to one card.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"
TARGET="${1:?usage: $0 <target>}"; shift
NATIVE_RELEASE=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --native-release) [ "$#" -ge 2 ] || exit 2; NATIVE_RELEASE="$(cd "$2" && pwd)"; shift 2 ;;
    *) echo "usage: $0 <target> [--native-release DIR]" >&2; exit 2 ;;
  esac
done
if [ -n "$NATIVE_RELEASE" ]; then
  for file in manifest generation generation.bin capsule.bin firmware.bin image.bin recovery-toc.bin; do
    [ -f "$NATIVE_RELEASE/$file" ] || { echo "missing $NATIVE_RELEASE/$file" >&2; exit 1; }
  done
  grep -qx "NATIVE_TARGET=$TARGET" "$NATIVE_RELEASE/manifest" || {
    echo "$NATIVE_RELEASE is not a native release for $TARGET" >&2; exit 1;
  }
fi
eval "$(python3 "$HERE/tools/device_profile.py" shell "$TARGET")"

WORK="$HERE/work/$TARGET"
SOURCE="$WORK/source.json"
BOOT_PREFIX="$WORK/boot-prefix.img"
OUT="$WORK/baseos-$TARGET.img"

[ -f "$SOURCE" ] || { echo "missing $SOURCE (run prepare-stock.sh $TARGET IMAGE)" >&2; exit 1; }
[ -f "$BOOT_PREFIX" ] || { echo "missing $BOOT_PREFIX (run prepare-stock.sh $TARGET IMAGE)" >&2; exit 1; }
[ -f "$WORK/rootfs.tar" ] || { echo "missing $WORK/rootfs.tar (run build-rootfs.sh $TARGET)" >&2; exit 1; }
python3 "$HERE/tools/source_manifest.py" verify "$SOURCE" "$TARGET"
eval "$(python3 "$HERE/tools/source_manifest.py" shell "$SOURCE" "$TARGET")"
[ "$(file_size "$BOOT_PREFIX")" -eq "$SOURCE_BOOT_PREFIX_SIZE" ] || {
  echo "boot-prefix size no longer matches source.json" >&2; exit 1;
}

# /usr/lib ships as a squashfs file that rcS mounts. Refuse a kernel that
# cannot mount it before composing an image that would boot without libraries.
python3 "$HERE/tools/lib_image.py" check "$TARGET"

"$HERE/tools/make-bootlogo.sh" "$TARGET"

# Sector math for the BaseOS 1.0 seven-partition A/B layout (docs/07).
# Slot A keeps the source firmware's rootfs start because the vendor U-Boot
# environment boots /dev/mmcblk0p5; slot B is the identically sized region
# straight after it and stays unallocated — it is the update target, and not
# being a partition is what keeps it invisible to desktop operating systems.
SLOT_START="$SOURCE_P5_START"
SLOT_SECTORS=$((512 * 2048))
FS_SECTORS="$SLOT_SECTORS"
UDISK_SECTORS=$((128 * 2048))
SLOT_B_START=$((SLOT_START + SLOT_SECTORS))
UDISK_START=$((SLOT_START + 2 * SLOT_SECTORS))
PRIMARY_START=$((UDISK_START + UDISK_SECTORS))
TOTAL_SECTORS=$((PRIMARY_START + 64 * 2048 + 2048))
PRIMARY_SECTORS=$((TOTAL_SECTORS - 4 - PRIMARY_START + 1))

# Host-native: image compose is mke2fs/mksquashfs/mkfs.vfat/GPT/tar only (aarch64
# work is earlier). Explicit host platform avoids a cached arm64 image on Intel.
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$WORK":/work -v "$HERE/tools":/tools:ro \
  -v "$HERE":/repo:ro \
  -v "${NATIVE_RELEASE:-$HERE}":/native-release:ro -e NATIVE_RELEASE="$NATIVE_RELEASE" \
  -v "$HERE/manifest":/manifest:ro \
  -e TARGET="$TARGET" -e OUT_NAME="$(basename "$OUT")" \
  -e TOTAL_SECTORS="$TOTAL_SECTORS" \
  -e P2_START="$SOURCE_P2_START" \
  -e SLOT_START="$SLOT_START" -e SLOT_SECTORS="$SLOT_SECTORS" \
  -e FS_SECTORS="$FS_SECTORS" \
  -e SLOT_B_START="$SLOT_B_START" \
  -e UDISK_START="$UDISK_START" -e UDISK_SECTORS="$UDISK_SECTORS" \
  -e PRIMARY_START="$PRIMARY_START" -e PRIMARY_SECTORS="$PRIMARY_SECTORS" \
  alpine:3.20 sh -euc '
  apk add -q e2fsprogs e2fsprogs-extra dosfstools mtools python3 sgdisk squashfs-tools

  OUT="/work/$OUT_NAME"
  rm -f "$OUT"

  # Preserve the StockMod boot chain and first four partitions, then resize.
  cp /work/boot-prefix.img "$OUT"
  truncate -s $((TOTAL_SECTORS * 512)) "$OUT"
  python3 /tools/mkgpt.py "$OUT" "$TOTAL_SECTORS" "$SLOT_SECTORS" "$UDISK_SECTORS"

  # Derive the matched gzip kernel/U-Boot pair from verified prepared inputs.
  # The original prepared cache stays untouched for provenance and recovery.
  python3 /tools/kernel_gzip.py apply "$TARGET" /work/boot-prefix.img "$OUT"
  if [ -n "$NATIVE_RELEASE" ]; then
    python3 /tools/source_manifest.py write-native /work/source.json "$TARGET" \
      /work/boot-prefix.img "$OUT" /native-release
    echo "native boot files written: generation $(cat /native-release/generation)"
  fi

  # The boot-resource partition is preserved except for BaseOS bootlogo.bmp.
  export MTOOLS_SKIP_CHECK=1
  mcopy -o -i "$OUT@@$((P2_START * 512))" /work/bootlogo.bmp ::/bootlogo.bmp
  echo "bootlogo replaced for $TARGET"

  # The vendor 4.9 kernel needs classic ext4 features and a journal. The
  # embedded vendor initramfs mounts p5 with data=ordered.
  EXT4_OPTS="^metadata_csum,^metadata_csum_seed,^64bit,^orphan_file"

  R=/tmp/r; rm -rf "$R"; mkdir "$R"
  tar -xf /work/rootfs.tar -C "$R"

  # /usr/lib moves into a squashfs file and the directory stays as its mount
  # point. The card reads about 20 MB/s and a frontend loads most of the GPU
  # library before its first frame. LZ4 halves those bytes and unpacks faster
  # than the card delivers; gzip and xz are smaller but cost more time to
  # unpack than the read they save. The 4.9 kernel has no squashfs xattrs.
  LIB_IMAGE=/usr/share/baseos/usr-lib.sqfs
  mksquashfs "$R/usr/lib" "$R$LIB_IMAGE" -comp lz4 -Xhc -b 131072 -noappend \
    -no-xattrs -no-exports -no-progress -mkfs-time 0 >/dev/null
  chmod 644 "$R$LIB_IMAGE"
  # Unpack it again and compare every name, mode, owner, link and content.
  listing() {
    (cd "$1" && find . | sort | while read -r p; do
      if [ -L "$p" ]; then echo "$p -> $(readlink "$p")"
      elif [ -f "$p" ]; then echo "$p $(stat -c "%a %u %g" "$p") $(sha256sum < "$p")"
      else echo "$p $(stat -c "%a %u %g %F" "$p")"
      fi
    done)
  }
  unsquashfs -q -n -d /tmp/lib-check "$R$LIB_IMAGE" >/dev/null
  listing "$R/usr/lib" > /tmp/lib.want
  listing /tmp/lib-check > /tmp/lib.got
  [ -s /tmp/lib.want ] && cmp /tmp/lib.want /tmp/lib.got || {
    echo "FATAL: the /usr/lib image does not match the directory"; exit 1
  }
  cp "$R$LIB_IMAGE" /tmp/usr-lib.sqfs
  find "$R/usr/lib" -mindepth 1 -delete
  rm -rf /tmp/lib-check /tmp/lib.got
  echo "/usr/lib image: $(wc -l < /tmp/lib.want) entries, $(stat -c %s /tmp/usr-lib.sqfs) bytes"

  if [ -n "$NATIVE_RELEASE" ]; then
    [ -f "$R/usr/sbin/native-health" ] && [ -x "$R/usr/sbin/native-health" ] || {
      echo "FATAL: native rootfs lacks /usr/sbin/native-health"; exit 1
    }
    mkdir -p "$R/etc/baseos" "$R/usr/share/baseos/native"
    for file in manifest generation capsule.bin firmware.bin image.bin recovery-toc.bin; do
      cp "/native-release/$file" "$R/usr/share/baseos/native/$file"
    done
    cp /native-release/generation.bin "$R/etc/baseos/native-generation"
    chown -R 0:0 "$R/etc/baseos" "$R/usr/share/baseos/native"
    chmod 755 "$R/etc/baseos" "$R/usr/share/baseos/native"
    chmod 644 "$R/etc/baseos/native-generation" "$R/usr/share/baseos/native/"*
    echo "native boot files in rootfs: generation $(cat /native-release/generation)"
  fi
  mke2fs -q -F -t ext4 -O "$EXT4_OPTS" -b 4096 -L rootfs -d "$R" \
    -E offset=$((SLOT_START * 512)) "$OUT" $((FS_SECTORS / 8))

  # Slot B is left as zeros on purpose: the first system update writes it.

  mke2fs -q -F -t ext4 -O "$EXT4_OPTS" -b 4096 -L userdata \
    -E offset=$((UDISK_START * 512)) "$OUT" $((UDISK_SECTORS / 8))

  mkfs.vfat -F 32 -n BASEOS -S 512 --offset "$PRIMARY_START" \
    "$OUT" $(((PRIMARY_SECTORS - 2048) / 2)) >/dev/null 2>&1

  # Structural and content verification.
  sgdisk -v "$OUT" | tail -4
  dd if="$OUT" of=/tmp/p5.img bs=512 skip="$SLOT_START" count="$SLOT_SECTORS" status=none
  e2fsck -fn /tmp/p5.img >/dev/null
  echo "p5 rootfs (slot A) ext4 OK"
  dd if="$OUT" of=/tmp/p6.img bs=512 skip="$UDISK_START" count="$UDISK_SECTORS" status=none
  e2fsck -fn /tmp/p6.img >/dev/null
  echo "p6 UDISK ext4 OK"

  # Slot B must be untouched zeros: a stray filesystem there would be applied
  # verbatim by the first gptslot flip.
  if [ "$(dd if="$OUT" bs=512 skip="$SLOT_B_START" count="$SLOT_SECTORS" status=none \
      | tr -d "\0" | wc -c)" -ne 0 ]; then
    echo "FATAL: rootfs slot B is not empty"; exit 1
  fi
  echo "slot B reserved and empty OK"

  debugfs -R "stat /init" /tmp/p5.img 2>/dev/null | grep -q "Type: regular" && echo "p5 /init OK"
  debugfs -R "stat /usr/sbin/gptgrow" /tmp/p5.img 2>/dev/null | grep -q Inode && echo "p5 gptgrow OK"
  debugfs -R "stat /usr/sbin/gptslot" /tmp/p5.img 2>/dev/null | grep -q Inode && echo "p5 gptslot OK"
  debugfs -R "stat /usr/sbin/expand-storage" /tmp/p5.img 2>/dev/null | grep -q "Mode:  0755" && echo "p5 expand-storage exec OK"
  debugfs -R "stat /usr/sbin/baseos-update" /tmp/p5.img 2>/dev/null | grep -q "Mode:  0755" && echo "p5 baseos-update exec OK"
  debugfs -R "cat /etc/baseos-release" /tmp/p5.img 2>/dev/null \
    | grep -qx "BASEOS_TARGET=$TARGET" && echo "p5 target identity OK"
  # Without the image the system has no libraries. Anything left in the
  # directory would sit unused under the mount.
  debugfs -R "dump $LIB_IMAGE /tmp/usr-lib.p5" /tmp/p5.img 2>/dev/null
  cmp /tmp/usr-lib.sqfs /tmp/usr-lib.p5 || { echo "FATAL: p5 lacks the /usr/lib image"; exit 1; }
  [ "$(debugfs -R "ls -p /usr/lib" /tmp/p5.img 2>/dev/null \
      | awk -F/ "NF > 5 && \$6 != \".\" && \$6 != \"..\"" | wc -l)" -eq 0 ] || {
    echo "FATAL: p5 /usr/lib is not an empty mount point"; exit 1
  }
  echo "p5 /usr/lib image OK"
  if [ -n "$NATIVE_RELEASE" ]; then
    debugfs -R "dump /etc/baseos/native-generation /tmp/generation.p5" /tmp/p5.img 2>/dev/null
    cmp /native-release/generation.bin /tmp/generation.p5 || {
      echo "FATAL: p5 native-generation does not match"; exit 1
    }
    for file in manifest generation capsule.bin firmware.bin image.bin recovery-toc.bin; do
      debugfs -R "dump /usr/share/baseos/native/$file /tmp/native.p5" /tmp/p5.img 2>/dev/null
      cmp "/native-release/$file" /tmp/native.p5 || { echo "FATAL: p5 native $file differs"; exit 1; }
    done
    [ "$(debugfs -R "ls -p /etc/baseos" /tmp/p5.img 2>/dev/null | awk -F/ "NF > 5 && \$6 != \".\" && \$6 != \"..\" { print \$6 }")" = native-generation ] || {
      echo "FATAL: p5 /etc/baseos holds more than the model generation"; exit 1
    }
    debugfs -R "stat /usr/sbin/native-health" /tmp/p5.img 2>/dev/null | grep -q "Mode:  0755" || {
      echo "FATAL: p5 native-health is not executable"; exit 1
    }
    rm -f /tmp/generation.p5 /tmp/native.p5
    echo "p5 native boot files OK"
    # Read every native area back out of the finished image.
    P4=$(sed -n "s/^BOOT_BOOT_START=//p" /work/boot-gzip/manifest)
    . /native-release/manifest
    for area in "capsule.bin $((NATIVE_CAPSULE_SECTOR * 512))" "recovery-toc.bin $((NATIVE_TOC_SECTOR * 512))" \
        "firmware.bin $((P4 * 512 + NATIVE_FIRMWARE_OFFSET))" "image.bin $((P4 * 512 + NATIVE_IMAGE_OFFSET))"; do
      set -- $area
      dd if="$OUT" of=/tmp/area.bin bs=512 skip=$(($2 / 512)) count=$(($(stat -c %s "/native-release/$1") / 512)) status=none
      cmp "/native-release/$1" /tmp/area.bin || { echo "FATAL: image $1 at byte $2 differs"; exit 1; }
      echo "image $1 at byte $2 OK"
    done
    rm -f /tmp/area.bin
  fi

  mcopy -i "$OUT@@$((P2_START * 512))" ::/bootlogo.bmp /tmp/embedded-bootlogo.bmp
  cmp /work/bootlogo.bmp /tmp/embedded-bootlogo.bmp
  minfo -i "$OUT@@$((PRIMARY_START * 512))" 2>/dev/null \
    | grep -E "disk size|cluster size" | head -2
  python3 /tools/source_manifest.py verify-image /work/source.json "$TARGET" \
    /work/boot-prefix.img "$OUT" /tmp/embedded-bootlogo.bmp ${NATIVE_RELEASE:+--native /native-release}

  rm -f /tmp/p5.img /tmp/p6.img /tmp/embedded-bootlogo.bmp /tmp/usr-lib.sqfs /tmp/usr-lib.p5
  ls -lh "$OUT"
  du -h "$OUT"
'

echo
echo "Image: $OUT"
echo "Flash: ./flash-card.sh $TARGET diskN"
