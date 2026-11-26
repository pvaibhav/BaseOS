#!/bin/sh
# Build/fetch the static aarch64 tools for the base OS rootfs:
#   work/tools/busybox        (Alpine busybox-static: init, ash, mount, insmod,
#                              udhcpc, hwclock, getty, poweroff, vi, top, ...)
#   work/tools/dropbearmulti  (static dropbear + dropbearkey + dbclient + scp)
#   work/tools/curl           (static HTTPS client used by RetroAchievements)
#   work/tools/ca-certificates.crt (TLS trust store)
#   work/tools/fbsplash       (framebuffer boot splash)
#   work/tools/gptgrow        (grow last GPT partition on first boot)
#   work/tools/gptslot        (A/B root-slot geometry + flip for updates)
#   work/tools/charger-wait   (blocking charger recovery input)
#   work/tools/boot-clock     (H700 hardware-counter boot-stage timing)
#   work/tools/native-health  (native boot health acceptance)
#   work/tools/baseos-regs    (read-only register view for baseos-diag)
#   work/tools/axp-off        (cut power at the PMIC; rcK's last step)
#   work/tools/sftp-server    (OpenSSH sftp subsystem child for dropbear)
#   work/tools/e2fsck         (root filesystem repair, run only when flagged)
#   work/tools/adbd           (Android adb daemon, USB-only, static)
#   work/tools/avahi-daemon   (mDNS responder for <hostname>.local, static)
# Must use --platform linux/arm64 so the produced binaries are aarch64 for the
# handheld (native on Apple Silicon; QEMU on Intel hosts).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"
TOOLS="$HERE/work/tools"
mkdir -p "$TOOLS"
# Do not let an obsolete reconnect helper linger in a reused tools directory.
rm -f "$TOOLS/usb-gadget-watch"

docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" -v "$TOOLS":/out alpine:3.20 sh -euc '
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
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" -v "$TOOLS":/out alpine:3.20 sh -euc '
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

# fbsplash: framebuffer boot splash (Lexend wordmark via freetype).
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" \
  -v "$TOOLS":/out -v "$HERE/src":/src:ro alpine:3.20 sh -euc '
  apk add -q build-base linux-headers pkgconf \
    freetype-dev freetype-static zlib-static libpng-static bzip2-static brotli-static
  gcc -static -O2 $(pkg-config --cflags freetype2) -o /out/fbsplash /src/fbsplash.c \
    $(pkg-config --static --libs freetype2)
  strip /out/fbsplash
'

# gptgrow: zero-dependency static tool that grows the last GPT partition to
# fill the card on first boot (see tools/gptgrow.c).
# gptslot: A/B root-slot arithmetic — derives the slot geometry from the GPT
# and flips partition 5 between the two halves (see tools/gptslot.c).
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" \
  -v "$TOOLS":/out -v "$HERE/tools":/src:ro alpine:3.20 sh -euc '
  apk add -q build-base linux-headers
  gcc -static -O2 -o /out/gptgrow /src/gptgrow.c
  strip /out/gptgrow
  gcc -static -O2 -I/src -o /out/gptslot /src/gptslot.c
  strip /out/gptslot
'

# Early boot helpers: PMIC shutdown, charger input, timing and native acceptance.
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" \
  -v "$TOOLS":/out -v "$HERE/src":/src:ro alpine:3.20 sh -euc '
  apk add -q build-base linux-headers
  gcc -static -O2 -Wall -Wextra -o /out/axp-off /src/axp-off.c
  gcc -static -O2 -Wall -Wextra -o /out/charger-wait /src/charger-wait.c
  gcc -static -O2 -Wall -Wextra -o /out/boot-clock /src/boot-clock.c
  gcc -std=c11 -static -O2 -Wall -Wextra -Werror -o /out/native-health /src/native-health.c
  gcc -std=c11 -static -O2 -Wall -Wextra -Werror -o /out/baseos-regs /src/baseos-regs.c
  strip /out/axp-off /out/charger-wait /out/boot-clock /out/native-health /out/baseos-regs
'

# e2fsck: the vendor initramfs used to check the root filesystem on every boot.
# BaseOS boots without it, so rcS runs this checker itself, and only when the
# filesystem carries recorded errors. Built alone and static; nothing else from
# e2fsprogs ships.
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" -v "$TOOLS":/out alpine:3.20 sh -euc '
  apk add -q build-base linux-headers ca-certificates tar xz
  E2FS_VERSION=1.47.2
  E2FS_SHA256=08242e64ca0e8194d9c1caad49762b19209a06318199b63ce74ae4ef2d74e63c
  cd /tmp
  wget -q "https://mirrors.edge.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v$E2FS_VERSION/e2fsprogs-$E2FS_VERSION.tar.xz"
  echo "$E2FS_SHA256  e2fsprogs-$E2FS_VERSION.tar.xz" | sha256sum -c -
  tar xf "e2fsprogs-$E2FS_VERSION.tar.xz"
  cd "e2fsprogs-$E2FS_VERSION"
  ./configure --disable-nls --disable-debugfs --disable-imager --disable-resizer \
    --disable-defrag --disable-uuidd --disable-fuse2fs --disable-e2initrd-helper \
    --disable-mmp --disable-tdb --disable-fsck LDFLAGS=-static >/dev/null
  make -j"$(nproc)" libs >/dev/null 2>&1
  make -j"$(nproc)" -C e2fsck e2fsck >/dev/null 2>&1
  strip e2fsck/e2fsck
  cp e2fsck/e2fsck /out/e2fsck
  chmod 755 /out/e2fsck
'

# sftp-server: dropbear 2024.85 ships the sftp subsystem execing
# SFTPSERVER_PATH=/usr/libexec/sftp-server, so the transport (owned by dropbear)
# hands each sftp session to this OpenSSH helper. It needs no crypto of its own,
# so build it without OpenSSL and without zlib for a small static binary.
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" -v "$TOOLS":/out alpine:3.20 sh -euc '
  apk add -q build-base linux-headers ca-certificates zlib-dev zlib-static
  OPENSSH_VERSION=10.4p1
  OPENSSH_SHA256=ef6026dd2aea8d56059638d5d3262902c892ceba9f88395835e0d06d3fb63238
  cd /tmp
  wget -q "https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/openssh-$OPENSSH_VERSION.tar.gz"
  echo "$OPENSSH_SHA256  openssh-$OPENSSH_VERSION.tar.gz" | sha256sum -c -
  tar xf "openssh-$OPENSSH_VERSION.tar.gz"
  cd "openssh-$OPENSSH_VERSION"
  ./configure --without-openssl --without-zlib --without-pam LDFLAGS=-static >/dev/null
  make sftp-server >/dev/null
  strip sftp-server
  cp sftp-server /out/sftp-server
  chmod 755 /out/sftp-server
'

# adbd: Android adb daemon (android-tools 4.2.2+git20130218). Built static for
# musl, out-of-tree via the Debian adbd makefile, exactly as Buildroot drives it
# in package/android-tools/android-tools.mk: unpack the Debian packaging into the
# source tree, apply the Debian quilt series, then apply the Buildroot patch
# series plus the BaseOS-local patches (see src/adbd-patches). The result serves
# adb over USB FunctionFS only (no network listener) and stays root.
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" \
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

# avahi-daemon: fully static (musl), without D-Bus. Runtime configuration limits
# publishing to the WiFi hostname/address records; no static service definitions
# are shipped. Build just libdaemon's library: its legacy tests need glibc headers.
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" \
  -v "$TOOLS":/out alpine:3.20 sh -euc '
  apk add -q build-base pkgconf \
    expat-dev expat-static libevent-dev libevent-static linux-headers

  LIBDAEMON_VERSION=0.14
  LIBDAEMON_SHA256=fd23eb5f6f986dcc7e708307355ba3289abe03cc381fc47a80bca4a50aa6b834
  cd /tmp
  wget -q "https://0pointer.de/lennart/projects/libdaemon/libdaemon-$LIBDAEMON_VERSION.tar.gz"
  echo "$LIBDAEMON_SHA256  libdaemon-$LIBDAEMON_VERSION.tar.gz" | sha256sum -c -
  tar xf "libdaemon-$LIBDAEMON_VERSION.tar.gz"
  cd "libdaemon-$LIBDAEMON_VERSION"
  # The 2008-era config.guess cannot parse a modern kernel release string.
  ./configure --build=aarch64-unknown-linux-gnu \
    --disable-shared --enable-static --prefix=/usr >/dev/null
  make -C libdaemon -j"$(nproc)" >/dev/null
  make -C libdaemon install >/dev/null

  AVAHI_VERSION=0.8
  AVAHI_SHA256=060309d7a333d38d951bc27598c677af1796934dbd98e1024e7ad8de798fedda
  cd /tmp
  wget -q "https://github.com/lathiat/avahi/releases/download/v$AVAHI_VERSION/avahi-$AVAHI_VERSION.tar.gz"
  echo "$AVAHI_SHA256  avahi-$AVAHI_VERSION.tar.gz" | sha256sum -c -
  tar xf "avahi-$AVAHI_VERSION.tar.gz"
  cd "avahi-$AVAHI_VERSION"
  ./configure --build=aarch64-unknown-linux-gnu \
    --prefix=/usr --sysconfdir=/etc --localstatedir=/var \
    --disable-dbus --disable-glib --disable-gobject --disable-gtk3 \
    --disable-qt4 --disable-qt5 \
    --disable-python --disable-python-dbus --disable-pygobject \
    --disable-gdbm \
    --disable-manpages --disable-doxygen-doc --disable-xmltoman \
    --disable-compat-howl --disable-compat-libdns_sd \
    --disable-shared --enable-static \
    --with-distro=none \
    LIBDAEMON_CFLAGS=-I/usr/include \
    LIBDAEMON_LIBS=-ldaemon >/dev/null
  make -j"$(nproc)" >/dev/null
  # libtool eats a bare -static on program links; relink the daemon fully
  # static so it carries no runtime library dependencies.
  touch avahi-daemon/main.c
  make -C avahi-daemon LDFLAGS=-all-static avahi-daemon >/dev/null
  strip avahi-daemon/avahi-daemon
  cp avahi-daemon/avahi-daemon /out/avahi-daemon
  chmod 755 /out/avahi-daemon
'

file "$TOOLS/busybox" "$TOOLS/dropbearmulti" "$TOOLS/curl" \
  "$TOOLS/fbsplash" "$TOOLS/gptgrow" "$TOOLS/gptslot" "$TOOLS/sftp-server" \
  "$TOOLS/adbd" "$TOOLS/axp-off" "$TOOLS/avahi-daemon" 2>/dev/null || true
[ -x "$TOOLS/gptslot" ] || { echo "gptslot build did not produce an executable" >&2; exit 1; }
[ -x "$TOOLS/charger-wait" ] || { echo "charger-wait build did not produce an executable" >&2; exit 1; }
[ -x "$TOOLS/axp-off" ] || { echo "axp-off build did not produce an executable" >&2; exit 1; }
[ -x "$TOOLS/native-health" ] || { echo "native-health build did not produce an executable" >&2; exit 1; }
file "$TOOLS/native-health" | grep -q "statically linked" \
  || { echo "native-health build is not static" >&2; exit 1; }
[ -x "$TOOLS/curl" ] || { echo "curl build did not produce an executable" >&2; exit 1; }
file "$TOOLS/curl" | grep -q "statically linked" \
  || { echo "curl build is not static" >&2; exit 1; }
[ -s "$TOOLS/ca-certificates.crt" ] \
  || { echo "curl CA bundle is missing or empty" >&2; exit 1; }
[ -x "$TOOLS/sftp-server" ] || { echo "sftp-server build did not produce an executable" >&2; exit 1; }
file "$TOOLS/sftp-server" | grep -q "statically linked" \
  || { echo "sftp-server build is not static" >&2; exit 1; }
[ -x "$TOOLS/adbd" ] || { echo "adbd build did not produce an executable" >&2; exit 1; }
file "$TOOLS/adbd" | grep -q "statically linked" \
  || { echo "adbd build is not static" >&2; exit 1; }
[ -x "$TOOLS/avahi-daemon" ] || { echo "avahi-daemon build did not produce an executable" >&2; exit 1; }
file "$TOOLS/avahi-daemon" | grep -q "statically linked" \
  || { echo "avahi-daemon build is not static" >&2; exit 1; }

# Record the sources these binaries came from so the build scripts can tell a
# reusable work/tools from a stale one.
"$HERE/tools/tools-stamp.sh" > "$TOOLS/.stamp"

ls -lh "$TOOLS"
