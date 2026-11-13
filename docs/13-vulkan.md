# 13 — Vulkan

The vendor Mali blob (Bifrost r20p0) has no Vulkan. BaseOS-spruce ships Mesa's
PanVK built for the vendor GPU kernel driver instead: the stock 4.9.170 kernel
and `mali_kbase` r20p0 (UK 11.17) stay as they are.

| File | What |
| --- | --- |
| `/usr/lib/aarch64-linux-gnu/libvulkan.so.1` | Khronos Vulkan loader |
| `/usr/lib/aarch64-linux-gnu/libvulkan_panfrost.so` | PanVK, Vulkan 1.3, not conformant |
| `/usr/share/vulkan/icd.d/panfrost_icd.aarch64.json` | Points the loader at PanVK |
| `/usr/share/licenses/mesa-panvk`, `/usr/share/licenses/vulkan-loader` | Licences |

GLES still comes from the vendor blob.

## Source

PanVK on kbase is [OursCodeur/mali-panvk](https://github.com/OursCodeur/mali-panvk)
(branch `rg35xx-g31`, a fork of JimVulkan/mali-panvk), pinned in
`vulkan/SOURCES`. `vulkan/patches/` adds:

- `VK_KHR_display` on `/dev/fb0` (`wsi_common_fbdev.c`, meson option
  `vulkan-wsi-fbdev`). One display, one plane, one mode, taken from the fb
  timings. Frames are copied into the hidden half of the double-height
  framebuffer, which is then panned to; the pan waits for vblank, so FIFO is
  free. The framebuffer contents are restored when the swapchain goes.
- Panel rotation: `BASEOS_PANEL_ROTATION_CCW` from `/etc/baseos-release`, or
  `MESA_VK_FBDEV_ROTATE`. Apps see the unrotated size (640x480 on the
  RG28XX) and the copy turns the frame.
- Cached, non-coherent host memory for the CPU-read swapchain images, with an
  invalidate before each copy. Uncached reads cost 33 ms per 720x720 frame.
- `noafbc` as the default `PANVK_DEBUG`: AFBC faults on this kbase path.
- The DRM-only display extensions are not advertised.

## Build

`build-vulkan.sh` builds into `work/vulkan` once for every target, and only
again when `vulkan/` changes. It cross-compiles in `vulkan/Dockerfile`: Ubuntu
24.04 tools (LLVM 18 for the shader compiler) against a Debian 11 arm64
sysroot (glibc 2.31, older than the userland's 2.35). g++ 13's static libstdc++ wants
two newer glibc symbols; `vulkan/compat.c` supplies them inside the driver.
It needs an x86-64 Docker host. An arm64 build host can use a `work/vulkan`
built elsewhere from the same `vulkan/`; the build reuses it.

## Measured on the RG CubeXX

| Test | Result |
| --- | --- |
| Compute and draw checks from the PanVK branch | 0 errors |
| `VK_KHR_display` FIFO swapchain, 720x720 | 59.6 fps |
| Same, rotated 90 or 270 | 30 fps: the rotated copy takes just over one frame |
| hakuX (Xbox), Halo 2 | Renders; CPU-bound |
