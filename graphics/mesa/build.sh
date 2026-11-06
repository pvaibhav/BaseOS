#!/bin/sh
set -eu
umask 022
cd /src
w=/src/work/mainline/graphics
s=$w/source
if [ "${BASEOS_MESA_PROFILE:-foundation}" = standard-egl ]; then
    w=/src/work/mainline/u4g
    export SOURCE_DATE_EPOCH=1791936000 LC_ALL=C
    export PKG_CONFIG_SYSROOT_DIR=/src/work/mainline/graphics/devel
    export PKG_CONFIG_PATH=$PKG_CONFIG_SYSROOT_DIR/usr/lib/pkgconfig
    export PKG_CONFIG_ALLOW_SYSTEM_CFLAGS=1 PKG_CONFIG_ALLOW_SYSTEM_LIBS=1
    export LD_LIBRARY_PATH=$PKG_CONFIG_SYSROOT_DIR/usr/lib
    export PATH=/src/work/mainline/graphics/build-clc/src/compiler/clc:/src/work/mainline/graphics/build-clc/src/compiler/spirv:$PATH
    meson setup --reconfigure --clearcache "$w/build-mesa" "$s/mesa-26.2.5" --prefix=/usr --libdir=lib --buildtype=release --wrap-mode=nofallback -Dauto_features=disabled -Dstrip=true -Dpkg_config_path=$PKG_CONFIG_PATH -Dgallium-drivers=panfrost -Dvulkan-drivers=[] -Dplatforms=[] -Degl=enabled -Degl-lib-suffix= -Degl-native-platform=surfaceless -Dgbm=enabled -Dgles1=disabled -Dgles2=enabled -Dopengl=false -Dglx=disabled -Dglvnd=disabled -Dllvm=disabled -Dmesa-clc=system -Dvideo-codecs=[] -Dgallium-va=disabled -Dgallium-rusticl=false -Dxmlconfig=disabled -Dexpat=disabled -Dzlib=disabled -Dzstd=disabled -Dshader-cache=disabled -Dtools=[] -Dcpp_link_args=-static-libstdc++
    ninja -C "$w/build-mesa" -j6
    DESTDIR=$w/devel meson install -C "$w/build-mesa" --no-rebuild
    exit
fi
export SOURCE_DATE_EPOCH=1791936000
export LC_ALL=C
export PKG_CONFIG_ALLOW_SYSTEM_CFLAGS=1
export PKG_CONFIG_ALLOW_SYSTEM_LIBS=1
export PYTHONDONTWRITEBYTECODE=1
export PKG_CONFIG_PATH=/opt/spirv/lib/pkgconfig
export LD_LIBRARY_PATH=$w/devel/usr/lib
base='--prefix=/usr --libdir=lib --buildtype=release --wrap-mode=nofallback -Dauto_features=disabled -Dstrip=true'
# Build-only CLC is separate; production uses its executables, never LLVM DSOs.
 meson setup --reconfigure --clearcache "$w/build-clc" "$s/mesa-26.2.5" $base -Dgallium-drivers=[] -Dvulkan-drivers=[] -Dplatforms=[] -Dglx=disabled -Dopengl=false -Degl=disabled -Dgbm=disabled -Dgles1=disabled -Dgles2=disabled -Dpkg_config_path=/opt/spirv/lib/pkgconfig -Dmesa-clc=enabled -Dspirv-tools=enabled -Dllvm=enabled -Dshared-llvm=enabled -Dinstall-mesa-clc=true
ninja -C "$w/build-clc" src/compiler/clc/mesa_clc src/compiler/spirv/vtn_bindgen2
export PATH=$w/build-clc/src/compiler/clc:$w/build-clc/src/compiler/spirv:$PATH
 meson setup --reconfigure --clearcache "$w/build-drm" "$s/libdrm-2.4.125" $base -Dtests=false -Dinstall-test-programs=false -Dudev=false
ninja -C "$w/build-drm"
DESTDIR=$w/devel meson install -C "$w/build-drm" --no-rebuild
export PKG_CONFIG_SYSROOT_DIR=$w/devel
export PKG_CONFIG_PATH=$w/devel/usr/lib/pkgconfig
 meson setup --reconfigure --clearcache "$w/build-mesa" "$s/mesa-26.2.5" $base -Dpkg_config_path=$w/devel/usr/lib/pkgconfig -Dgallium-drivers=panfrost -Dvulkan-drivers=[] -Dplatforms=[] -Degl=enabled -Degl-lib-suffix=_mesa -Degl-native-platform=surfaceless -Dgbm=enabled -Dgles1=enabled -Dgles2=enabled -Dopengl=false -Dglx=disabled -Dglvnd=disabled -Dllvm=disabled -Dmesa-clc=system -Dvideo-codecs=[] -Dgallium-va=disabled -Dgallium-rusticl=false -Dxmlconfig=disabled -Dexpat=disabled -Dzlib=disabled -Dzstd=disabled -Dshader-cache=disabled -Dtools=drm-shim -Dcpp_link_args=-static-libstdc++
ninja -C "$w/build-mesa" -j 6
DESTDIR=$w/devel meson install -C "$w/build-mesa" --no-rebuild
