#!/bin/sh
# Build USB audio modules for a target whose vendor kernel lacks the driver.
# Targets with the driver built in, or with an already verified build, skip it.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:?usage: $0 <target>}"
[ "$#" -eq 1 ] || { echo "usage: $0 <target>" >&2; exit 2; }
if python3 "$HERE/tools/usb_audio_modules.py" current "$TARGET"; then
  exit 0
fi
BUILD="$(python3 "$HERE/tools/usb_audio_modules.py" prepare "$TARGET")"
if [ -n "$BUILD" ]; then
  rm -f "$BUILD"/*.ko
  CACHE="$HERE/work/audio-build"
  mkdir -p "$CACHE"
  # Same pinned compiler and kernel source as build-audio-module.sh.
  docker run --rm --platform linux/arm64 \
    -v "$BUILD":/out -v "$CACHE":/cache \
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
    export ARCH=arm64 CROSS_COMPILE=aarch64-nextui-linux-gnu-
    # olddefconfig drops vendor-only options either way. Record that baseline
    # so verification can check that USB audio changes nothing else.
    cp /out/kernel.config .config
    make HOSTCFLAGS="-O2 -fcommon" olddefconfig >/out/baseline.log 2>&1
    cp .config /out/baseline.config
    cp /out/kernel.config .config
    echo CONFIG_SND_USB_AUDIO=m >> .config
    make HOSTCFLAGS="-O2 -fcommon" olddefconfig modules_prepare >/out/prepare.log 2>&1
    cp .config /out/effective.config
    cp /out/Module.symvers Module.symvers
    make HOSTCFLAGS="-O2 -fcommon" M=sound/core modules >/out/build-core.log 2>&1
    # modpost must see snd-hwdep exports, or snd-usb-audio imports
    # snd_hwdep_new without a symbol version or a module dependency.
    make HOSTCFLAGS="-O2 -fcommon" M=sound/usb \
      KBUILD_EXTRA_SYMBOLS="$PWD/sound/core/Module.symvers" modules >/out/build-usb.log 2>&1
    for module in sound/core/snd-hwdep.ko sound/usb/snd-usbmidi-lib.ko sound/usb/snd-usb-audio.ko; do
      aarch64-nextui-linux-gnu-strip --strip-debug "$module"
      cp "$module" /out/
    done
    touch /out/.built
    '
fi
python3 "$HERE/tools/usb_audio_modules.py" verify "$TARGET"
