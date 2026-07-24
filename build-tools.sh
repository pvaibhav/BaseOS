#!/bin/sh
# Build/fetch the static aarch64 tools for the base OS rootfs:
#   work/tools/busybox        (Alpine busybox-static: init, ash, mount, insmod,
#                              udhcpc, hwclock, getty, poweroff, vi, top, ...)
#   work/tools/dropbearmulti  (static dropbear + dropbearkey + dbclient + scp)
#   work/tools/curl           (static HTTPS client used by RetroAchievements)
#   work/tools/ca-certificates.crt (TLS trust store)
#   work/tools/fbsplash       (framebuffer boot splash)
#   work/tools/gptgrow        (grow last GPT partition on first boot)
#   work/tools/adbd           (Android adb daemon, USB-only, static)
# Runs entirely in a linux/arm64 container (native on Apple Silicon).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$HERE/work/tools"
mkdir -p "$TOOLS"

docker run --rm --platform linux/arm64 -v "$TOOLS":/out alpine:3.20 sh -euc '
  apk add -q busybox-static
  cp /bin/busybox.static /out/busybox

  apk add -q build-base zlib-dev zlib-static curl
  cd /tmp
  curl -fsSLO https://matt.ucc.asn.au/dropbear/releases/dropbear-2024.85.tar.bz2
  tar xf dropbear-2024.85.tar.bz2
  cd dropbear-2024.85
  ./configure --enable-static --disable-lastlog --disable-wtmp >/dev/null
  make -j"$(nproc)" PROGRAMS="dropbear dropbearkey dbclient scp" MULTI=1 >/dev/null
  cp dropbearmulti /out/dropbearmulti
  chmod 755 /out/busybox /out/dropbearmulti
'

# curl: NextUI's HTTP layer invokes the CLI for RetroAchievements. Build it in
# a clean container so it cannot inherit configure state from another tool.
# The pinned static binary carries no StockMod or frontend ABI dependency.
docker run --rm --platform linux/arm64 -v "$TOOLS":/out alpine:3.20 sh -euc '
  apk add -q build-base ca-certificates tar xz perl \
    openssl-dev openssl-libs-static zlib-dev zlib-static
  CURL_VERSION=8.21.0
  CURL_SHA256=aa1b66a70eace83dc624508745646c08ae561de512ab403adffb93ac87fc72e6
  cd /tmp
  wget -q "https://curl.se/download/curl-$CURL_VERSION.tar.xz"
  echo "$CURL_SHA256  curl-$CURL_VERSION.tar.xz" | sha256sum -c -
  tar xf "curl-$CURL_VERSION.tar.xz"
  cd "curl-$CURL_VERSION"
  ./configure \
    --disable-shared --enable-static --with-openssl --with-zlib \
    --without-libpsl --without-brotli --without-zstd --without-libidn2 \
    --without-nghttp2 --disable-ldap --disable-ldaps --disable-rtsp \
    --disable-dict --disable-telnet --disable-tftp --disable-pop3 \
    --disable-imap --disable-smb --disable-smtp --disable-gopher \
    --disable-mqtt --disable-manual >/dev/null
  make -j"$(nproc)" LDFLAGS=-all-static >/dev/null
  strip src/curl
  cp src/curl /out/curl
  cp /etc/ssl/certs/ca-certificates.crt /out/ca-certificates.crt
  chmod 755 /out/curl
'

# fbsplash: framebuffer boot splash (Lexend wordmark via freetype), see
# src/fbsplash.c.
docker run --rm --platform linux/arm64 \
  -v "$TOOLS":/out -v "$HERE/src":/src:ro alpine:3.20 sh -euc '
  apk add -q build-base linux-headers pkgconf \
    freetype-dev freetype-static zlib-static libpng-static bzip2-static brotli-static
  gcc -static -O2 $(pkg-config --cflags freetype2) -o /out/fbsplash /src/fbsplash.c \
    $(pkg-config --static --libs freetype2)
  strip /out/fbsplash
'

# gptgrow: zero-dependency static tool that grows the last GPT partition to
# fill the card on first boot (see tools/gptgrow.c).
docker run --rm --platform linux/arm64 \
  -v "$TOOLS":/out -v "$HERE/tools":/src:ro alpine:3.20 sh -euc '
  apk add -q build-base linux-headers
  gcc -static -O2 -o /out/gptgrow /src/gptgrow.c
  strip /out/gptgrow
'

# adbd: Android adb daemon (android-tools 4.2.2+git20130218). Built static for
# musl, out-of-tree via the Debian adbd makefile, exactly as Buildroot drives it
# in package/android-tools/android-tools.mk: unpack the Debian packaging into the
# source tree, apply the Debian quilt series, then apply the Buildroot patch
# series plus the BaseOS-local patches (see src/adbd-patches). The result serves
# adb over USB FunctionFS only (no network listener) and stays root.
docker run --rm --platform linux/arm64 \
  -v "$TOOLS":/out -v "$HERE/src/adbd-patches":/patches:ro alpine:3.20 sh -euc '
  apk add -q build-base linux-headers pkgconf ca-certificates tar xz patch \
    openssl-dev openssl-libs-static zlib-dev zlib-static bsd-compat-headers
  AT_VER=4.2.2+git20130218
  SITE=https://launchpad.net/ubuntu/+archive/primary/+files
  ORIG_SHA256=9bfba987e1351b12aa983787b9ae4424ab752e9e646d8e93771538dc1e5d932f
  DEB_SHA256=73c3078de3e44d8a3cadf7a360863c63155d9d558c2f0933cf38ad901a3f5998
  cd /tmp
  wget -q "$SITE/android-tools_$AT_VER.orig.tar.xz"
  wget -q "$SITE/android-tools_$AT_VER-3ubuntu41.debian.tar.gz"
  echo "$ORIG_SHA256  android-tools_$AT_VER.orig.tar.xz" | sha256sum -c -
  echo "$DEB_SHA256  android-tools_$AT_VER-3ubuntu41.debian.tar.gz" | sha256sum -c -
  tar xf "android-tools_$AT_VER.orig.tar.xz"
  cd android-tools
  tar xf "/tmp/android-tools_$AT_VER-3ubuntu41.debian.tar.gz"
  while read -r p; do [ -z "$p" ] && continue
    patch -g0 -p1 -E -i "debian/patches/$p" >/dev/null
  done < debian/patches/series
  for p in /patches/0*.patch; do patch -g0 -p1 -E -i "$p" >/dev/null; done
  # musl carries crypt() in libc, so drop the -lcrypt the makefile assumes; link
  # libcrypto statically. Build noise (upstream -Wall warnings) is suppressed.
  mkdir -p build-adbd
  make SRCDIR="$PWD" -C build-adbd -f "$PWD/debian/makefiles/adbd.mk" \
    CC=gcc LDFLAGS=-static LIBS="-lc -lpthread -lz -lcrypto" >/dev/null 2>&1
  strip build-adbd/adbd
  cp build-adbd/adbd /out/adbd
  chmod 755 /out/adbd
'

file "$TOOLS/busybox" "$TOOLS/dropbearmulti" "$TOOLS/curl" \
  "$TOOLS/fbsplash" "$TOOLS/gptgrow" "$TOOLS/adbd" 2>/dev/null || true
[ -x "$TOOLS/curl" ] || { echo "curl build did not produce an executable" >&2; exit 1; }
file "$TOOLS/curl" | grep -q "statically linked" \
  || { echo "curl build is not static" >&2; exit 1; }
[ -s "$TOOLS/ca-certificates.crt" ] \
  || { echo "curl CA bundle is missing or empty" >&2; exit 1; }
[ -x "$TOOLS/adbd" ] || { echo "adbd build did not produce an executable" >&2; exit 1; }
file "$TOOLS/adbd" | grep -q "statically linked" \
  || { echo "adbd build is not static" >&2; exit 1; }
ls -lh "$TOOLS"
