#!/bin/sh
set -eu
unset MESA_GLES_VERSION_OVERRIDE MESA_GL_VERSION_OVERRIDE MESA_EXTENSION_OVERRIDE GALLIUM_DRIVER LIBGL_ALWAYS_SOFTWARE
cd /src
w=/src/work/mainline/graphics
cc graphics/mesa/query.c -I"$w/devel/usr/include" -L"$w/runtime/usr/lib" -Wl,-rpath-link,"$w/runtime/usr/lib" -lEGL_mesa -lGLESv2 -o "$w/query"
shim=$w/build-mesa/src/panfrost/drm-shim/libpanfrost_noop_drm_shim.so
# A process-local fake DRM device; no render call and no device host interaction.
timeout 20 env LD_LIBRARY_PATH="$w/runtime/usr/lib:$w/harvested" LD_PRELOAD="$shim" LIBGL_DRIVERS_PATH="$w/runtime/usr/lib/dri" PAN_GPU_ID=7093 EGL_PLATFORM=surfaceless MESA_LOADER_DRIVER_OVERRIDE=panfrost "$w/harvested/ld-linux-aarch64.so.1" "$w/query" > "$w/noop-query.txt" 2>&1
