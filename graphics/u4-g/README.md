# U4-G consumption contract (host only)

Status: host implementation built; exact stock SDL SONAME/export metadata gate passed.
Hardware qualified, durable install eligible, boot authorized: **false**.
No menu, rendering, pairing, orientation, input, audio, suspend or timing proof.
Parent joins U4-F/K and requests one combined gpt6.1-sol review before hardware.

Use `work/mainline/u4g/runtime/usr/lib` as the trial's `/usr/lib` layer.
The SDL file to bind over the frontend's bundled SDL is
`runtime/usr/lib/libSDL2-2.0.so.0.2800.5` (SONAME `libSDL2-2.0.so.0`).
Development headers, link names and pkg-config files are in `devel/usr`.
Keep `gbm/dri_gbm.so` at `/usr/lib/gbm/dri_gbm.so` with libgallium alongside
EGL/GLES/GBM/DRM. Do not bind only EGL or substitute a renamed vendor library.
Runtime inventory, SHA256s, symlinks, sizes and full versioned ELF closure are
in `work/mainline/u4g/verification.json`; input/output/source identity is in
`build-identity.json`. Runtime includes seven stripped DSOs, no GLES1, LLVM,
software rasterizer or dynamic C++ runtime. BaseOS supplies the audited glibc,
libm, libgcc and loader providers; they are not copied into the runtime.

Public frontend API is unchanged. Use `SDL_VIDEODRIVER=kmsdrm`, ES 3.0 and
standard `libEGL.so.1`, `libGLESv2.so.2`, `libgbm.so.1`, `libdrm.so.2`.
For the private trial prefix set `GBM_BACKENDS_PATH=/opt/u4/g/lib/gbm`;
`LD_LIBRARY_PATH` alone does not redirect GBM's backend loader.
Both SDL video backends are compiled. Mali's private entrypoints are resolved
only by its selected backend; SDL has no unconditional EGL/GLES/libmali NEEDED.
Stock mali behavior is retained in its original source. KMSDRM keeps upstream
connected-card discovery, not a card0 assumption. Host source/ELF checks do not
prove capless DRM master acquisition or actual node pairing.

`SDL_ROTATION=0..3` uses the fork's centered-coordinate transformations:
`(x,y)`, `(y,-x)`, `(-x,-y)`, `(-y,x)`. Portrait defaults to 1, landscape to 0.
The imported GBM window image has its top row at texture t=0, so UV conversion
also reverses the source vertical coordinate. Copying the vendor formula without
that origin conversion produced the reflection observed on the real device.
Odd rotations advertise swapped logical modes (RG28 640x480 vs physical480x640).
Set rotation before video initialization; live hint changes are unsupported.
Out-of-range values fall back to zero. KMS does not use the old Mali blitter's
ION allocation, private pixmap ABI, scaler options or worker thread.
Rotation zero uses upstream direct EGL-to-GBM presentation without a rotate draw.
Nonzero uses one draw from a stable app-sized GBM/EGL surface through a native
GBM EGLImage in a private unshared GLES2 context into physical scanout. Current
app context and separate draw/read surfaces are restored, including failures.
The rotation context swaps the physical surface before restoring the app context;
the outer presentation path skips its direct swap for rotated windows.
No CPU pixel copy/readback. App and private surfaces live until surface teardown.

Two conservative `glFinish` handoffs ensure producer completion before import
and sampling completion before source BO release; these serialize GPU work and
have unmeasured latency. They avoid incorrectly assuming cross-context explicit
fences. Scanout uses Mesa/kmsro implicit DMA-buf synchronization. Pending page
flips complete before old BO release or teardown. Failed queue/FB/lock/swap paths
retain the current scanout and release only the abandoned new BO. Irrecoverable
DRM event drain failure during destruction terminates the process via `_exit(1)`
to avoid releasing live scanout or leaving dangling event callback data. Parent
supervision must handle a failed child. No hardware claim is made about this path.

`BASEOS_PRESENT_TRACE=<existing writable tmpfs directory>` enables bounded JSONL
at `present.<pid>.jsonl`. Default off; at most 128 frame entries and their completion
records. The final completion closes the trace even if the destination cannot be
opened; later page flips do not keep appending under sequence 128.
Events: `entry`, `modeset-return`, `flip`. Each records frame seq,
swap-entry MONOTONIC_RAW ns, observation MONOTONIC_RAW ns, CNTVCT, and DRM event
sequence/sec/usec. DRM event timebase is not asserted equal to MONOTONIC_RAW.
The first modeset is an ioctl return, not a pageflip event or proven visibility.
Logging opens/writes/closes a file and adds overhead only when enabled. There is
no framebuffer hash or menu identity. Neither first event nor a future sparse
framebuffer hash can establish visible-menu time without independent evidence.
This JSONL interface deliberately supersedes the architect's proposed JSON/hash.

Mesa uses its compiled static pipe-loader kmsro fallback for display-only
`sun4i-drm`. `gbm/dri_gbm.so` links libgallium directly; old separate DRI aliases
are not needed by this Mesa 26 GBM path. kmsro enumerates compatible render-capable
platform nodes, selects Panfrost, and uses renderonly dumb scanout allocation
with DRM PRIME handle-to-fd export and fd-to-handle import. Source build commands
include kmsro, renderonly, Panfrost resource and pipe-loader units. This is compiled
support, not proof that DE33 and the G31 pair or share usable buffers on hardware.

Pins: SDL `d4a7d7503524cc469fe775242f3f925d4dd56c88`, source archive SHA256
`6485a6178a4a1554a30d7f7f68b55c3af4d2a52daea489bb09d3b479626c4d1a`,
plus unchanged `graphics/sdl2/sdl2-h700.patch` and the KMS patch/helper files.
Mesa26.2.5/libdrm2.4.125/toolchain pins remain `graphics/mesa/pins.json` and its
package locks. Existing source and build-only compiler/dependencies are reused.
Standard Mesa builds into U4-G; original foundation outputs remain separate.

Reproduce: `python3 tools/build-h700-sdl.py` (existing Mesa container required).
The performed full builds were the equivalent direct commands:

```
docker exec -e BASEOS_MESA_PROFILE=standard-egl baseos-h700-mesa-build sh /src/graphics/mesa/build.sh
docker exec baseos-h700-mesa-build sh /src/graphics/sdl2/build.sh
```

Configuration/compile failures were corrected incrementally, without clean
rebuild loops. Final affected SDL objects were rebuilt with `make -j6` and
`make install DESTDIR=/src/work/mainline/u4g/devel` in `build-sdl`.
SDL autotools lacks local include tracking; the recipe touches only the two
KMS translation units so helper changes rebuild them. Build logs are in U4-G.
`python3 tools/build-h700-sdl.py --finalize` verifies and freezes the already logged build;
`--cached` checks full source/recipe/runtime identity and ELF closure without
rebuilding. `python3 tests/test-h700-sdl-kmsdrm.py` compiles actual helper/swap C
with boundary stubs: rotations/corners, defaults, buffer release order/failures,
and deliberate wrong-rotation and missing-wait negative controls. These tests
are not a GPU/display emulator or a platform capability result.

The exact stock SDL baseline is the parent-supplied ABI facts at
`work/mainline/frontend-abi/stock-sdl-76de58c8.json`, from a transient read of
`/mnt/sdcard/.system/h700/lib/libSDL2-2.0.so.0` on stock RG28XX serial
`0c00105562458931c52`. The parent verified SHA256
`76de58c80f9a6d901fc5563711b9edc869a759c9446c5a6cd798f4c6774f7577`,
8,026,720 bytes and AArch64; no baseline ELF is retained or required.
Verification compares the rebuilt ELF directly against these facts: identical
SONAME and all 842 export names, binding, type, visibility, version name/index
and hidden-version fields. All match, with no missing exports or metadata
mismatches. The facts file hash is included in the guarded build identity.
This establishes the requested exported ABI coverage, not frontend behavior.
The local `ce4b4e2e...` NextUI payload is distinct and was not used as the stock
baseline. No optional comparison against that different build is claimed.
