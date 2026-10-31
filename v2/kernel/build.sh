#!/bin/sh
# Build the BaseOS 2 kernel in the v2-kbuild container (Linux git tree in volume
# v2-kernel at /k/linux, build dir /k/build). Copies Image + DTB to work/v2/kernel
# and saves the config and patch series into v2/kernel.
set -eu
cd "$(dirname "$0")/../.."
docker exec v2-kbuild bash -euc '
cd /k/linux
# Host helpers need the container libraries, not the frontend cross sysroot.
unset PKG_CONFIG_PATH PKG_CONFIG_SYSROOT_DIR
make -s -j8 O=/k/build ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  KBUILD_BUILD_TIMESTAMP=2026-10-31 KBUILD_BUILD_USER=baseos KBUILD_BUILD_HOST=baseos \
  Image modules allwinner/sun50i-h700-anbernic-rg28xx-baseos.dtb \
  allwinner/sun50i-h700-anbernic-rgsp-baseos.dtb
mkdir -p /repo/work/v2/kernel
cp /k/build/arch/arm64/boot/Image /repo/work/v2/kernel/Image
cp /k/build/arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rg28xx-baseos.dtb /repo/work/v2/kernel/rg28xx.dtb
cp /k/build/arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rgsp-baseos.dtb /repo/work/v2/kernel/rgsp.dtb
# Keep optional drivers outside the boot image.
rm -rf /repo/work/v2/kernel/modules
make -s O=/k/build ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  INSTALL_MOD_PATH=/repo/work/v2/kernel/modules modules_install
cp /k/build/.config /repo/v2/kernel/config
rm -rf /repo/v2/kernel/patches && git format-patch -q --no-signature -o /repo/v2/kernel/patches HEAD~$(($(git rev-list --count HEAD) - 1))
'
ls -la work/v2/kernel
