#!/bin/sh
# Build an audited H700 audio module against the target's vendor config/CRCs.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:?usage: $0 <target>}"
[ "$#" -eq 1 ] || { echo "usage: $0 <target>" >&2; exit 2; }
OUT="$HERE/work/$TARGET/audio-module"
if python3 "$HERE/tools/audio_module.py" current "$TARGET"; then
  exit 0
fi
python3 "$HERE/tools/audio_module.py" prepare "$TARGET"
CACHE="$HERE/work/audio-build"
mkdir -p "$CACHE"
# This compiler is the GCC 8.3 H700 toolchain used to validate the vendor ABI.
docker run --rm --platform linux/arm64 \
  -v "$OUT":/out -v "$CACHE":/cache \
  ghcr.io/loveretro/h700-toolchain@sha256:bf5d8f9f5442cad789aa77176bc059ec823caefa3258bf30780eee681c777119 \
  sh -euc '
  if ! command -v bc >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq bc
  fi
  ARCHIVE=/cache/linux-4.9.170.tar.xz
  if [ ! -f "$ARCHIVE" ]; then
    curl -fL https://cdn.kernel.org/pub/linux/kernel/v4.x/linux-4.9.170.tar.xz -o "$ARCHIVE.tmp"
    mv "$ARCHIVE.tmp" "$ARCHIVE"
  fi
  echo "33887b40fc8e0b71f423bb7afe112a4ae190378145f4a3f3892c563b7e43131d  $ARCHIVE" | sha256sum -c -
  tar -xJf "$ARCHIVE" -C /tmp
  cd /tmp/linux-4.9.170
  cp /out/kernel.config .config
  export ARCH=arm64 CROSS_COMPILE=aarch64-nextui-linux-gnu-
  make HOSTCFLAGS="-O2 -fcommon" olddefconfig modules_prepare >/out/prepare.log 2>&1
  cp /out/Module.symvers Module.symvers
  make HOSTCFLAGS="-O2 -fcommon" M=/out/module modules >/out/build.log 2>&1
  aarch64-nextui-linux-gnu-strip --strip-debug /out/module/h700_speaker_amp.ko /out/module/h700_late_usb.ko /out/module/h700_rumble_pwm.ko
  '
python3 "$HERE/tools/audio_module.py" verify "$TARGET"
