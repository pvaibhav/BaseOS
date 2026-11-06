# H700 common Mesa userspace (host only)

Pinned stable Mesa 26.2.5 and minimal libdrm 2.4.125, built for Linux AArch64 in an isolated, digest-pinned Ubuntu 22.04 ARM64 container. Official release checksums and URLs, source-tree hashes and the image identity are in `pins.json`. The libdrm version is sufficient for Panfrost and avoids unrelated driver libraries.

From the repository root:

```sh
python3 tools/build-h700-mesa.py --prepare # first container setup only
python3 tools/build-h700-mesa.py           # cached build, restage and verify
```

Docker/network access requires the host's sandbox authorization. No host packages are installed. `--rootfs PATH` selects the harvested ABI archive; the default is the original prepared `work/rg28xx/rootfs.tar`. Only seven ABI-provider tar members are read, not the entire rootfs. Container preparation installs exact additional package versions from `container-packages.lock`; if repository versions become unavailable, preparation fails rather than silently upgrading. Python generator packages are pinned in `prepare.sh`. Cached source trees are checked against hashes in the tracked pins, container image/package versions checked, Meson reconfigured, Ninja run, and runtime libraries restaged and verified on every invocation. Build scripts and exact Meson flags live here, without changing global builders.

Outputs are under `work/mainline/graphics/`: `source/`, `build-{spirv,clc,drm,mesa}/`, development install `devel/`, and separate **production runtime** `runtime/usr/lib/`. The report is `mesa-verification.json`. Successful source downloads are removed after extraction. No firmware, SD card or device was accessed.

Production settings: Panfrost only; EGL enabled with suffix `_mesa`, default native platform surfaceless; GBM, GLES1 and GLES2/3 enabled; GLVND, desktop OpenGL/GLX, LLVM, X11/Wayland, Vulkan, codecs, VA, Rusticl, XML/expat, zlib/zstd and disk shader cache disabled. All optional features default disabled. Mesa 26's kmsro support is implicit in the DRM Gallium build (not a valid `gallium-drivers=kmsro` option); `pipe_loader_drm.c` falls back to kmsro for display-only DRM drivers. DRM EGL and GBM remain enabled for later DE33 integration. No software rasterizer is shipped. LLVM 15 and SPIRV-Tools 2024.1 generate internal Panfrost shaders in the separate build-only CLC tree; production uses `mesa-clc=system` and `llvm=disabled`. C++ standard-library support is linked statically from GCC 11.4; real remaining dynamic dependencies are verified against harvested providers.

`verify.py` verifies AArch64, SONAMEs, selected EGL/GLES/GBM exports, every strong undefined dynamic symbol (including symbol versions) against the actual transitive providers, all GLIBC version requirements <=2.35, absence of libmali dependencies, and resolution with the harvested dynamic loader. The ABI evidence is an older BaseOS 1.4.0 archive with Ubuntu GLIBC 2.35-0ubuntu3, **not a fresh live-device observation**. These checks do not establish unchanged frontend compatibility.

A future BaseOS EGL adapter can `dlopen("libEGL_mesa.so.1", RTLD_NOW|RTLD_LOCAL)` and resolve a private function table with `dlsym(handle, ...)`. Its own `libEGL.so.1` must not depend on another DSO with that same SONAME. Mesa's GLES libraries retain their normal SONAMEs. No adapter, legacy ION, ES-version override or frontend replacement is implemented here.

The optional bounded capability query is `docker exec baseos-h700-mesa-build sh graphics/mesa/query.sh`. It uses the build-tree Panfrost noop DRM shim, never copied into runtime. Source `src/panfrost/drm-shim/panfrost_noop.c` parses `PAN_GPU_ID` with `strtol(...,16)`; `7093` means **0x7093**, not decimal 7093. Mesa normalizes this raw ID to `PAN_PROD_ID(7,0,3)`, listed as G31 in `src/panfrost/model/pan_model.c`. The probe creates an ES3.1 context and queries strings/extensions, then tries an unmodified ES3.2 request, with a 20-second limit. No drawing or benchmarks. Any successful report is only a no-hardware capability query, never rendering, hardware or speed proof.

All hardware, joined, deployment and durable qualification flags remain false. Device rendering, GBM/KMS presentation, panel/DRAM detection, unchanged frontend census and performance remain separate gates.

Verified runtime size: **21,344,952 bytes (20.36 MiB)** across seven real DSOs; `libgallium-26.2.5.so` accounts for 20,707,504 bytes. Highest new bundled GLIBC requirement: **2.34**. The remaining external providers are harvested `libc.so.6`, `libm.so.6`, `libgcc_s.so.1` and `ld-linux-aarch64.so.1`; no dynamic libstdc++, LLVM, libmali or compression dependency. Full hashes, sizes, SONAMEs, version requirements, loader output and extension strings are in the JSON report.

The no-hardware query passed: EGL 1.5, `Mali-G31 MC4 (Panfrost)`, `OpenGL ES 3.1 Mesa 26.2.5`; the unmodified ES3.2 request was rejected with `EGL_BAD_MATCH` (0x3009). MC4 is the shim's assumed core count, not an H700 observation. The shim printed unknown GET_PARAM values 8 and 43; retain those diagnostics as emulation limits. No rendering was attempted.

The cached public-builder rerun passed. Clean rebuilds of production Mesa and libdrm reproduced all seven runtime DSO SHA-256 hashes exactly; see `work/mainline/graphics/reproducibility.json` for commands, scope and the recovered shell-run diagnostic. Generator tools were reused. This establishes same-input, same-toolchain reproducibility in the fixed `/src` container path, not independent toolchain bootstrap reproducibility. The base is the existing cached `baseos-portmaster-builder` Ubuntu 22.04 image identified by its actual digest; another host needs that image content. The owned container can be stopped after building; cached runs start it automatically.

The independent R1 review passed the current runtime ABI checks but found that the cached guard omitted foundational package versions. The parent added 18 compiler/linker/libc/static-C++ package pins (76 total), checked the actual cached container, and tested 22 changed/missing-package cases with the real guard. No library rebuild or restaging was needed; all seven DSO hashes remain unchanged. Original review/report snapshots remain historical; `work/mainline/graphics/mesa-review-resolution.json` binds this parent closure and current guard hashes. It is not a new independent review.
