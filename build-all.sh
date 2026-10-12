#!/bin/sh
# Build every model's release artifacts from prepared inputs.
# Usage: ./build-all.sh [target ...]
#
# Produces, per target, the two files a release publishes:
#   work/<target>/baseos-<target>-<version>.img.zip   flash once
#   work/<target>/baseos-<target>-<version>.bosupd    every update after that
#
# Inputs come from fetch-prepared.sh (or prepare-stock.sh), so this needs no
# StockMod .img. The shared tools are built once and reused; the per-target work
# is rootfs -> QEMU userspace smoke test -> image -> update payload, which is the
# same sequence build-stockmod.sh runs, minus the preparation step.
#
# The structural gates on the prepared inputs live in verify-target.sh and run at
# prepare or pack time, not here: they check assumptions about vendor firmware,
# which does not change between code builds.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGETS="$*"
[ -n "$TARGETS" ] || TARGETS="$(python3 "$HERE/tools/device_profile.py" list)"

VERSION="$(tr -d ' \n' < "$HERE/VERSION")"
[ -n "$VERSION" ] || { echo "VERSION is empty" >&2; exit 1; }
command -v zip >/dev/null 2>&1 || { echo "zip is required to package images" >&2; exit 1; }

# Validate every target and its inputs up front, so a typo or a missing restore
# is not discovered forty minutes into a ten-model build.
for target in $TARGETS; do
  python3 "$HERE/tools/device_profile.py" shell "$target" >/dev/null
  work="$HERE/work/$target"
  for artifact in source.json boot-prefix.img stock-harvest.tar; do
    [ -f "$work/$artifact" ] || {
      echo "missing $work/$artifact — run ./fetch-prepared.sh $target" >&2
      exit 1
    }
  done
done

# Same staleness rule as build-stockmod.sh: existence alone cannot see a source
# edit, and a stale binary beside fresh overlay scripts fails only on a device.
if [ ! -x "$HERE/work/tools/busybox" ] \
  || [ ! -x "$HERE/work/tools/dropbearmulti" ] \
  || [ ! -x "$HERE/work/tools/curl" ] \
  || [ ! -x "$HERE/work/tools/fbsplash" ] \
  || [ ! -x "$HERE/work/tools/gptgrow" ] \
  || [ ! -x "$HERE/work/tools/gptslot" ] \
  || [ ! -x "$HERE/work/tools/sftp-server" ] \
  || [ ! -x "$HERE/work/tools/adbd" ] \
  || [ ! -x "$HERE/work/tools/usb-gadget-watch" ] \
  || [ ! -x "$HERE/work/tools/avahi-daemon" ] \
  || [ ! -x "$HERE/work/tools/boot-clock" ] \
  || ! "$HERE/tools/tools-stamp.sh" | cmp -s - "$HERE/work/tools/.stamp"; then
  "$HERE/build-tools.sh"
else
  echo "Reusing shared tools in $HERE/work/tools"
fi

for target in $TARGETS; do
  echo
  echo "=============================== $target"
  "$HERE/build-rootfs.sh" "$target"
  "$HERE/test-boot-qemu.sh" "$target"
  "$HERE/build-image.sh" "$target"
  "$HERE/build-update.sh" "$target"

  work="$HERE/work/$target"
  archive="$work/baseos-$target-$VERSION.img.zip"
  rm -f "$archive"
  # -j so the archive holds a bare baseos-<target>.img rather than work/<target>/,
  # and -X so no host filesystem metadata rides along into a published file.
  zip -q -j -X "$archive" "$work/baseos-$target.img"
  echo "packaged $(basename "$archive")"
done

echo
echo "=== release artifacts for $VERSION ==="
for target in $TARGETS; do
  work="$HERE/work/$target"
  for artifact in "baseos-$target-$VERSION.img.zip" "baseos-$target-$VERSION.bosupd"; do
    [ -f "$work/$artifact" ] || continue
    printf "  %-44s %s\n" "$artifact" "$(du -h "$work/$artifact" | cut -f1)"
  done
done
echo
# Checksums of the published *files*, which is what a user can check after
# downloading. This is not the image-sha256 build-update.sh prints: that one
# covers the decompressed slot image inside the payload and is what the update
# engine verifies on the device after writing it (docs/07 §4). Both are worth
# publishing; only this one is checkable with shasum against the download.
echo "=== SHA-256 of each published file (for the release page) ==="
for target in $TARGETS; do
  for artifact in "baseos-$target-$VERSION.img.zip" "baseos-$target-$VERSION.bosupd"; do
    path="$HERE/work/$target/$artifact"
    [ -f "$path" ] || continue
    if command -v sha256sum >/dev/null 2>&1; then
      sum="$(sha256sum "$path" | cut -d' ' -f1)"
    else
      sum="$(shasum -a 256 "$path" | cut -d' ' -f1)"
    fi
    printf "  %s  %s\n" "$sum" "$artifact"
  done
done
