#!/bin/sh
set -eu
umask 022
cd /src
w=/src/work/v2/mesa-cache
s=/src/work/mainline/graphics/source/mesa-26.2.5
deps=/src/work/mainline/graphics/devel
root=/src/work/mainline/alpha1/os/root
mkdir -p "$w/sysroot/usr/include" "$w/sysroot/usr/lib/pkgconfig" "$w/sysroot/usr/lib/aarch64-linux-gnu" "$w/runtime/gbm"
# Keep build dependencies here so the existing builds stay untouched.
cp -a "$deps/usr/include/libdrm" "$w/sysroot/usr/include/"
cp -a "$deps/usr/include/xf86drm.h" "$deps/usr/include/xf86drmMode.h" "$deps/usr/include/libsync.h" "$w/sysroot/usr/include/"
cp -a "$deps/usr/lib/pkgconfig/libdrm.pc" "$w/sysroot/usr/lib/pkgconfig/"
cp -L "$deps/usr/lib/libdrm.so.2.125.0" "$w/sysroot/usr/lib/libdrm.so.2.125.0"
ln -sf libdrm.so.2.125.0 "$w/sysroot/usr/lib/libdrm.so.2"
ln -sf libdrm.so.2 "$w/sysroot/usr/lib/libdrm.so"
cp /usr/include/zlib.h /usr/include/zconf.h "$w/sysroot/usr/include/"
cp /usr/lib/aarch64-linux-gnu/pkgconfig/zlib.pc "$w/sysroot/usr/lib/pkgconfig/"
# Link against the target's zlib with the container's development headers.
cp -L "$root/usr/lib/aarch64-linux-gnu/libz.so.1" "$w/sysroot/usr/lib/aarch64-linux-gnu/libz.so.1"
ln -sf libz.so.1 "$w/sysroot/usr/lib/aarch64-linux-gnu/libz.so"
export SOURCE_DATE_EPOCH=1791936000 LC_ALL=C PYTHONDONTWRITEBYTECODE=1
export PKG_CONFIG_SYSROOT_DIR=$w/sysroot
export PKG_CONFIG_PATH=$PKG_CONFIG_SYSROOT_DIR/usr/lib/pkgconfig
export PKG_CONFIG_ALLOW_SYSTEM_CFLAGS=1 PKG_CONFIG_ALLOW_SYSTEM_LIBS=1
export LD_LIBRARY_PATH=$PKG_CONFIG_SYSROOT_DIR/usr/lib
export PATH=/src/work/mainline/graphics/build-clc/src/compiler/clc:/src/work/mainline/graphics/build-clc/src/compiler/spirv:$PATH
start=$(date +%s)
# These are the standard-egl options, with the cache and zlib enabled.
meson setup --reconfigure --clearcache "$w/build" "$s" --prefix=/usr --libdir=lib --buildtype=release --wrap-mode=nofallback -Dauto_features=disabled -Dstrip=true -Dpkg_config_path=$PKG_CONFIG_PATH -Dgallium-drivers=panfrost -Dvulkan-drivers=[] -Dplatforms=[] -Degl=enabled -Degl-lib-suffix= -Degl-native-platform=surfaceless -Dgbm=enabled -Dgles1=disabled -Dgles2=enabled -Dopengl=false -Dglx=disabled -Dglvnd=disabled -Dllvm=disabled -Dmesa-clc=system -Dvideo-codecs=[] -Dgallium-va=disabled -Dgallium-rusticl=false -Dxmlconfig=disabled -Dexpat=disabled -Dzlib=enabled -Dzstd=disabled -Dshader-cache=enabled -Dshader-cache-default=true -Dtools=[] -Dcpp_link_args=-static-libstdc++
ninja -C "$w/build" -j6
DESTDIR=$w/stage meson install -C "$w/build" --no-rebuild
for name in libEGL.so.1.0.0 libGLESv2.so.2.0.0 libgallium-26.2.5.so libgbm.so.1.0.0 gbm/dri_gbm.so; do
    cp -L "$w/stage/usr/lib/$name" "$w/runtime/$name"
done
# libdrm is unchanged; use the same library already shipped in the root.
cp -L "$root/usr/lib/baseos-mainline/libdrm.so.2.125.0" "$w/runtime/libdrm.so.2.125.0"
end=$(date +%s)
printf '%s\n' "$((end - start))" > "$w/build-seconds.txt"
python3 /src/v2/graphics/mesa-cache-verify.py
