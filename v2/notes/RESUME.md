# Resume BaseOS 2 development

State on 6 November 2026, when the repository was cleaned up to return to 1.4.x
work. The last build tested on hardware ran on the RG34XX SP (play mode, Slot)
and the RG28XX; see `performance-slot.md` and `PLAN-beta.md` for what works.

## Start here

- Branch `feature/h700-mainline`. Read [PLAN-beta.md](../PLAN-beta.md) (work
  packages, 1.4.0 parity matrix), then [CONTRACT.md](../CONTRACT.md).
- Everything current is in `v2/`. The firmware build also compiles sources
  outside it, all committed: `experiments/h700-boot-speed/{spl,native}` (the
  1.5.x fast-boot loader, with the open-firmware additions BaseOS 2 needs),
  `experiments/h700-mainline/{helper,include,u3,u3-join}`, and the TF-A pin and
  patches in `firmware/tf-a/`. `graphics/mesa` and `graphics/sdl2` are the
  recipes behind the Mesa and SDL libraries in the root image.
- Results and decisions are in this folder: `performance-slot.md`, `hdmi.md`,
  `usb-host.md`, `firmware-all-models.md`, `firmware-fleet-results.md`,
  `rgsp-radios.md`, `sd-uhs.md`, `spruce-audit.md`. Commit history on this
  branch records the path taken.

## Setting up a fresh checkout

1. Stock inputs (per model): `./fetch-prepared.sh <model>` restores the stock
   boot prefix from the published "Prepared artifacts 20260914" release, then
   `python3 tools/kernel_gzip.py prepare <model> work/<model>/boot-prefix.img
   work/<model>/boot-gzip` and `sh tools/make-bootlogo.sh <model>`. Both outputs
   were checked byte-identical to the ones the tested firmware was built from.
2. Frozen inputs: the root image starts from a staging tree built by an earlier
   prototype tool that no longer exists, so it is kept as a file outside the
   repository: `~/Code/Me/baseos2-frozen-inputs-20261106.tar.zst`. Extract it at
   the checkout root (`tar --zstd -xf ... -C <checkout>`). It holds:
   - `work/mainline/alpha1/os/root` — root staging tree (1.5.3 userspace, NextUI
     and MinArch libraries); `v2/os/build.py` starts from it
   - `work/v2/mesa-cache/runtime` — Mesa 26.2.5 libraries with the shader cache
   - `work/mainline/u4g/devel/usr/lib/libSDL2-2.0.so.0.2800.5` — KMS SDL
   - `work/mainline/interactive/media/payload/interactive-final/image.bin` —
     base of development payloads (`v2/dev/build.py`)
3. U-Boot: `work/h700-loader/mainline/u-boot-v2026.10.tar.gz` (pinned by hash
   in `v2/firmware/build.py`; download from the U-Boot project).
4. TF-A: clone the commit pinned in `firmware/tf-a/source.json` to
   `work/mainline/firmware/source`.
5. Firmware: `python3 v2/firmware/build.py v2a` (RG28XX) or
   `python3 v2/firmware/build.py v2-<model> --target <model>`. The RG34XX SP
   firmware rebuilt this way in a clean checkout was byte-identical to the one
   installed on the device.
6. Radio firmware and tools: `v2/os/radio/README.md` (pinned downloads).

## Kernel

Source lives in Docker volume `v2-kernel` (`/k/linux`, build output `/k/build`),
used by container `v2-kbuild`:

    docker run -d --name v2-kbuild -v v2-kernel:/k -v <checkout>:/repo \
      baseos-alpha1-squashfs:local sleep infinity

(the image is the h700 toolchain image with squashfs-tools added). The tree is
Linux 7.3.1 imported as one commit plus the 43 commits exported in
`v2/kernel/patches/` (tip `d286f8b9d`, config `v2/kernel/config`). Without the
volume: unpack linux-7.3.1 from kernel.org, commit it, `git am
v2/kernel/patches/*`. `sh v2/kernel/build.sh` builds the kernel, model DTBs and
modules into `work/v2/kernel/` and re-exports the patches.

## Frontends (separate repositories, not pushed)

- NextUI: branch `baseos2-min` in the NextUI repository.
- Slot: branch `baseos2` in the Slot repository.
