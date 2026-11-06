#!/bin/sh
set -eu
w=/src/work/mainline/u4g
f=/src/work/mainline/graphics
s=$w/source/SDL-malifbdev-rot-d4a7d7503524cc469fe775242f3f925d4dd56c88
export PKG_CONFIG_ALLOW_SYSTEM_CFLAGS=1 PKG_CONFIG_ALLOW_SYSTEM_LIBS=1
export SOURCE_DATE_EPOCH=1791936000 LC_ALL=C
export PKG_CONFIG_SYSROOT_DIR=$w/devel PKG_CONFIG_PATH=$w/devel/usr/lib/pkgconfig:$f/devel/usr/lib/pkgconfig
export LD_LIBRARY_PATH=$w/devel/usr/lib:$f/devel/usr/lib
mkdir -p "$w/build-sdl"
cd "$w/build-sdl"
CFLAGS="-O2 -I$w/devel/usr/include -I$f/devel/usr/include" LDFLAGS="-L$w/devel/usr/lib" "$s/configure" --prefix=/usr --libdir=/usr/lib \
 --enable-video-mali --enable-video-kmsdrm --enable-video-kmsdrm-shared \
 --enable-alsa --enable-alsa-shared --enable-video-opengles --disable-video-opengl \
 --disable-video-x11 --disable-video-wayland --disable-pulseaudio --disable-jack \
 --disable-esd --disable-arts --disable-nas --disable-sndio --disable-dbus --disable-ibus \
 --disable-fcitx --disable-hidapi --disable-libudev --disable-power --enable-filesystem \
 --disable-sensor --disable-locale --enable-loadso --enable-shared --disable-static \
 --disable-libsamplerate-shared --disable-video-vulkan
# SDL autotools does not track included local .inc/.h dependencies.
touch "$s/src/video/kmsdrm/SDL_kmsdrmvideo.c" "$s/src/video/kmsdrm/SDL_kmsdrmopengles.c"
make -j6
make install DESTDIR="$w/devel"
strip --strip-unneeded "$w"/devel/usr/lib/libSDL2-2.0.so.0.*
