#!/bin/sh
# Runs inside the vulkan/Dockerfile image. /src is this directory, /out
# receives a tree that build-rootfs.sh copies onto the root filesystem.
set -eu
. /src/SOURCES

B=/tmp/vk
rm -rf "$B"
mkdir -p "$B"
cd "$B"

git init -q panvk
git -C panvk fetch -q --depth 1 "$PANVK_REPO" "$PANVK_COMMIT"
git -C panvk checkout -q FETCH_HEAD
git -C panvk apply /src/patches/*.patch
git clone -q --depth 1 -b "$VULKAN_TAG" https://github.com/KhronosGroup/Vulkan-Headers.git
git clone -q --depth 1 -b "$VULKAN_TAG" https://github.com/KhronosGroup/Vulkan-Loader.git

common="-Dgallium-drivers= -Dplatforms= -Dgles1=disabled -Dgles2=disabled
  -Degl=disabled -Dglx=disabled -Dgbm=disabled -Dtools= -Dvideo-codecs=
  -Dbuild-tests=false -Dpanfrost-rust=false -Dvulkan-drivers=panfrost
  --force-fallback-for=libdrm -Dallow-fallback-for=libdrm -Dlibdrm:default_library=static"

cd "$B/panvk"
meson setup build-host -Dbuildtype=release -Db_ndebug=true \
  -Dmesa-clc=enabled -Dprecomp-compiler=enabled -Dllvm=enabled $common
ninja -C build-host src/compiler/clc/mesa_clc \
  src/compiler/spirv/vtn_bindgen2 src/panfrost/clc/panfrost_compile
export PATH="$PWD/build-host/src/compiler/clc:$PWD/build-host/src/compiler/spirv:$PWD/build-host/src/panfrost/clc:$PATH"

aarch64-linux-gnu-gcc --sysroot=/sysroot -O2 -fPIC -c /src/compat.c -o /tmp/compat.o
meson setup build-arm64 --cross-file /src/aarch64.ini -Dbuildtype=release -Db_ndebug=true \
  -Dllvm=disabled -Dmesa-clc=system -Dprecomp-compiler=system \
  -Dvulkan-wsi-fbdev=true -Dlibunwind=disabled -Dlmsensors=disabled \
  -Dzstd=disabled -Dexpat=disabled $common
ninja -C build-arm64 src/panfrost/vulkan/libvulkan_panfrost.so

cd "$B"
cat > arm64.cmake <<'EOF'
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_SYSROOT /sysroot)
set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
EOF
cmake -S Vulkan-Headers -B build-headers -G Ninja -DCMAKE_INSTALL_PREFIX="$B/vkh" >/dev/null
cmake --install build-headers >/dev/null
cmake -S Vulkan-Loader -B build-loader -G Ninja -DCMAKE_TOOLCHAIN_FILE="$B/arm64.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DVULKAN_HEADERS_INSTALL_DIR="$B/vkh" -DCMAKE_FIND_ROOT_PATH="$B/vkh" \
  -DBUILD_WSI_XCB_SUPPORT=OFF -DBUILD_WSI_XLIB_SUPPORT=OFF -DBUILD_WSI_WAYLAND_SUPPORT=OFF \
  -DBUILD_WSI_DIRECTFB_SUPPORT=OFF -DUSE_GAS=OFF -DBUILD_TESTS=OFF
ninja -C build-loader

LIB=/out/root/usr/lib/aarch64-linux-gnu
ICD=/out/root/usr/share/vulkan/icd.d
LIC=/out/root/usr/share/licenses
mkdir -p "$LIB" "$ICD" "$LIC/mesa-panvk" "$LIC/vulkan-loader"
aarch64-linux-gnu-strip -o "$LIB/libvulkan_panfrost.so" panvk/build-arm64/src/panfrost/vulkan/libvulkan_panfrost.so
aarch64-linux-gnu-strip -o "$LIB/libvulkan.so.1" "$(readlink -f build-loader/loader/libvulkan.so.1)"
ln -s libvulkan.so.1 "$LIB/libvulkan.so"
cat > "$ICD/panfrost_icd.aarch64.json" <<'EOF'
{
  "file_format_version": "1.0.0",
  "ICD": {
    "library_path": "/usr/lib/aarch64-linux-gnu/libvulkan_panfrost.so",
    "api_version": "1.3.358"
  }
}
EOF
cp panvk/LICENSE panvk/THIRD_PARTY_NOTICES.md "$LIC/mesa-panvk/"
cp -R panvk/LICENSES "$LIC/mesa-panvk/"
cp Vulkan-Loader/LICENSE.txt "$LIC/vulkan-loader/"

# Nothing newer than the sysroot's glibc 2.31
for f in "$LIB/libvulkan_panfrost.so" "$LIB/libvulkan.so.1"; do
  need="$(aarch64-linux-gnu-objdump -T "$f" | grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -1)"
  echo "$(basename "$f") needs $need"
  [ "$(printf '%s\nGLIBC_2.31\n' "$need" | sort -V | tail -1)" = GLIBC_2.31 ] \
    || { echo "FATAL: $f needs $need" >&2; exit 1; }
done
