# Contributing to BaseOS

## Branches and pull requests

| Branch | What it holds | Send pull requests here for |
|---|---|---|
| `main` | Development: the 1.5.x line and all new work | New features and most fixes |
| `stable` | The 1.4.x release line | Fixes for people on 1.4.x |
| `feature/mainline-kernel` | BaseOS 2 on mainline Linux, experimental | BaseOS 2 work only |

Start your branch from the branch you will send it to. A branch started from
another one carries commits that are not yours into the pull request.

If a fix is needed in both 1.5.x and 1.4.x, send it to `main` and say in the
description that 1.4.x needs it too. A maintainer copies it to `stable` after
merging.

The release marked Latest on the Releases page is built from `stable`.
Pre-releases are built from `main`.

## What you need

- A Mac with Docker or OrbStack. Every build step runs in an unprivileged
  Alpine container, so the Mac needs no sudo and no loop mounts.
- No vendor firmware. `./fetch-prepared.sh` downloads the prepared inputs for
  every model as one bundle, instead of a 12 GB vendor image per model. You
  need a vendor image only to add a model or to move a model to a new vendor
  release.

Steps that build or run device programs use `linux/arm64` containers, which run
natively on Apple Silicon and under emulation on Intel Macs. Preparation, image
assembly, boot logo generation and the QEMU smoke tests use the Mac's own
architecture, so they are never emulated.

## Building

```sh
./fetch-prepared.sh
./build-all.sh
```

For every model, this produces the two files a release publishes:

```
work/<target>/baseos-<target>-<version>.img.zip   flash once
work/<target>/baseos-<target>-<version>.bosupd    every update after that
```

Both scripts accept a list of targets. To work on one model:

```sh
./fetch-prepared.sh rg40xxv
./build-all.sh rg40xxv
./flash-card.sh rg40xxv diskN
```

`build-all.sh` builds the root filesystem, runs a QEMU smoke test of it, builds
the card image and the update file, and packages them. It rebuilds the shared
tools when a required binary is missing, or when a C or header file in `src/`
or `tools/` or an adb patch changes. It does not notice changes to build recipes
or pinned dependencies, so run `./build-tools.sh` yourself after those.

Each step also runs on its own:

```sh
./build-tools.sh
./build-rootfs.sh rg40xxv
./test-boot-qemu.sh rg40xxv
./build-image.sh rg40xxv
./build-update.sh rg40xxv
```

The version number comes from the `VERSION` file at the top of the repository.
`build-rootfs.sh` writes it, with the output of `git describe`, into
`/etc/baseos-release` and `/etc/os-release` on the device.

### Targets

| Device | Target ID |
|---|---|
| Anbernic RG28XX | `rg28xx` |
| Anbernic RG34XX | `rg34xx` |
| Anbernic RG34XX SP | `rg34xxsp` |
| Anbernic RG35XX Plus / RG35XX 2024 | `rg35xxplus` |
| Anbernic RG35XX H | `rg35xxh` |
| Anbernic RG35XX Pro | `rg35xxpro` |
| Anbernic RG35XX SP | `rg35xxsp` |
| Anbernic RG40XX H | `rg40xxh` |
| Anbernic RG40XX V | `rg40xxv` |
| Anbernic RG CubeXX | `rgcubexx` |
| Anbernic RG SP | `rgsp` |

Each target's settings are in `devices.json`. `panel_rotation_ccw` is how far
the boot logo is turned so that it shows upright on a panel mounted sideways.
It is `90` on the RG28XX, whose 480×640 panel is held in landscape, and `0` on
every other model. The vendor's own boot logo decides the value, so read
section 2.1 of [docs/04-boot-splash.md](docs/04-boot-splash.md) before changing
it.

## Testing

Run the tests that cover the files you changed. These run on the Mac:

```sh
./test-prepare-stock.sh
./test-expand-storage.sh
python3 tests/test-verify-harvest.py
python3 tests/test-prepared-cache.py
python3 tests/test-audio-gating.py
python3 tests/test-audio-module.py
python3 tests/test-strip-gpu-module.py
./tests/test-baseos-config.sh
./tests/test-h700-speaker-amp-load.sh
./tests/test-baseos-mdns.sh
./tests/test-boot-splash-policy.sh
./tests/test-frontend-session.sh
./tests/test-boot-migration.sh
./tests/test-root-mount.sh
./tests/test-usr-lib-image.sh
./tests/test-input-dir-time.sh
./tests/test-wifi-init.sh
./tests/test-systemctl-wifi.sh
./tests/test-poweroff-policy.sh
./tests/test-axp-off.sh
./tests/test-splash-rotation.sh
./tests/test-baseos-ntp.sh
./tests/test-timedatectl.sh
./tests/test-rtc-utc-policy.sh
./tests/test-gpt-slot.sh
./tests/test-update-apply.sh
./tests/test-boot-menu-held.sh
./tests/test-usb-gadget-adb.sh
./tests/test-usb-storage-mode.sh
./tests/test-power-intent.sh
```

These need the prepared inputs for every model, so run `./fetch-prepared.sh`
with no targets first:

```sh
python3 tests/test-kernel-gzip.py
python3 tests/test-late-usb.py
python3 tests/test-lib-image.py
```

These need a build of the target:

```sh
./test-boot-qemu.sh rg40xxv          # after build-rootfs.sh
./test-update-roundtrip.sh rg40xxv   # after build-image.sh and build-update.sh
```

To check a flashed device:

```sh
./validate-on-device.sh rg40xxv DEVICE_IP [ROOT_PASSWORD]
```

`validate-on-device.sh` logs in to the device over SSH, so turn on Wi-Fi in
NextUI's settings first. The root password defaults to `root`. It checks the
system identity, partition layout and update slots, boot speed, hardware,
NextUI, the developer services and resource use, then prints how many checks
passed and failed.

## Boot timing

The README's startup times include loading the kernel but not loading the
frontend, and it reports frontend load times separately. Change those figures
only after repeated measurements show a real difference. Label times measured
from kernel start separately from times measured from power-on.

`validate-on-device.sh` fails on any model whose kernel-to-frontend handoff
takes longer than 2.50 seconds. [Boot performance](docs/09-boot-profiling.md)
explains what to measure, how to compare two builds, and the GPU module
optimization.

## Prepared inputs

`prepare-stock.sh` makes three files per target from a vendor image:
`boot-prefix.img`, `stock-harvest.tar` and `source.json`. The published bundle
holds the first two. Git holds each target's `source.json`, which records the
size and SHA-256 of the other two:

```
manifest/prepared/<target>.json   each target's source.json
manifest/prepared/bundle.sha256   SHA-256 of the published bundle
manifest/prepared/bundle.url      where the bundle is published
```

`fetch-prepared.sh` checks the download against `bundle.sha256`.
`build-rootfs.sh` and `build-image.sh` check each file against its `source.json`
on every build, so a damaged download fails at the same point as a bad
preparation.

The bundle only saves you the vendor downloads. `prepare-stock.sh` makes the same
files from the vendor image, with the same hashes. To use a bundle already on
disk instead of downloading one, run `./fetch-prepared.sh --from <bundle>`. It
refuses to overwrite a target you prepared yourself from different firmware
unless you pass `--force`.

## Stock and StockMod images

`prepare-stock.sh` accepts an Anbernic stock image or a StockMod image and makes
the same output from either, so there is one build path and one card layout. It
tells the two apart by the partition table, not by the file name:

| | partition 1 | p2 to p7 |
|---|---|---|
| StockMod | `special`, 64 MiB empty ext4 | start at 204800 |
| stock | `Roms`, 2 GiB user-visible FAT32 | start ~1.94 GiB higher |

Some StockMod BASE images have no final `primary` partition. This happens in
trimmed images and in full-disk images with a valid backup GPT, such as StockMod
v4. `prepare-stock.sh` accepts both.

Before anything else, `prepare-stock.sh` converts a stock partition table into
the StockMod one. It removes `Roms`, puts an empty 64 MiB `special` partition in
its place, and moves every later partition down by the difference in size. That
puts p2 to p5 at exactly the StockMod offsets. It copies `boot0`, U-Boot, `env`
and `boot` unchanged.

This is safe because StockMod makes the same change and its cards boot. In an
RG34XX SP stock image and StockMod image compared side by side, `env` and `boot`
are byte-identical after the move, and U-Boot is unmodified. U-Boot finds
partitions by their GPT names for `partitions=` and the root by partition number
for `root=`, never by address. The
[boot contract](docs/07-partition-layout-and-updates.md#1-boot-contract) has the
details. The only other change StockMod makes to the boot chain is one nibble of
`dram_para[28]` and its checksum. That is a DRAM timing trim the move does not
need, so a card made from stock keeps the stock timings.

`source.json` records the image kind in `source_layout` (`stock` or `stockmod`),
keeps the vendor's original partition table in `source.partitions`, and records
the converted table in `layout`. Preparing from a stock image needs the whole
card image, because the root filesystem files come from partition 5.
`prepare-stock.sh` refuses a truncated stock image and says why.

## Adding a device model

Adding a model and taking a new vendor firmware are the only tasks that need a
vendor `.img`, and only the person doing them needs it, once.

```sh
# First add the model to devices.json: id, model, stockmod_prefix,
# model_string, bootlogo_width, bootlogo_height, panel_rotation_ccw.
./prepare-stock.sh rgsp /path/to/RGSP-....img
./verify-target.sh rgsp
./cache-pack.sh
./build-all.sh rgsp && ./validate-on-device.sh rgsp DEVICE_IP
```

`stockmod_prefix` matches vendor files named `<MODEL>-...`. Set the optional
`filename_alias` only for an image someone renamed by hand.

`cache-pack.sh` runs `verify-target.sh` on every target, writes the new bundle
to `work/prepared/`, and prints the `gh release create` and `git add` commands
that publish it. Bundle releases are tagged by date, as `prepared-YYYYMMDD`.

## Taking a new vendor firmware

Each target's committed `source.json` pins one vendor firmware. A new Anbernic
release changes nothing until you take it for that target:

```sh
./prepare-stock.sh rg40xxv /path/to/RG40XXV-<new>.img
git diff manifest/prepared/rg40xxv.json    # exactly what changed
./verify-target.sh rg40xxv
./cache-pack.sh
./build-all.sh rg40xxv && ./validate-on-device.sh rg40xxv DEVICE_IP
```

Review that diff. It shows every change to the partition geometry, the file
hashes and the vendor image's own details in one small file.

`verify-target.sh` checks the things a new firmware could break without any
visible error:

- The partition layout matches the recorded one.
- p1 `special` is an empty ext4 filesystem.
- p3 `env` still boots `/dev/mmcblk0p5`, and still leaves `partitions=` for
  U-Boot to fill in from the GPT names.
- The p4 kernel still exports every symbol the harvested modules use, with
  matching modversions CRCs.

The last check matters most. If a new kernel's exports have changed, the
harvested modules fail to load, and the device ends up with no GPU or no Wi-Fi
and nothing else to show why. `tools/kernel_abi.py` reads the export table
straight from the vendor `Image`, which comes without a `vmlinux` or
`Module.symvers`. Its docstring explains how. It can also check one target's
modules against another target's kernel:

```sh
python3 tools/kernel_abi.py check rg34xxsp --modules-from rg40xxv
```

To prepare and build from a firmware image in one step, without the bundle:

```sh
./build-stockmod.sh /path/to/firmware rg40xxv
```

## The audio module

`build-rootfs.sh` runs `build-audio-module.sh <target>`, which builds the kernel
module that stops speaker and headphone pops, and packages it. The build uses a
pinned kernel source archive and compiler, the target's own kernel
configuration, and symbol versions recovered from the vendor kernel. It keeps
its own cache, apart from `work/tools`, and rebuilds when the module sources,
build inputs or reviewed profile change.

`src/h700-speaker-amp/profiles.json` holds the exact kernel hashes and private
symbol offsets for each target. Before updating it for a new firmware, check the
codec, GPIO and ASoC layouts. A matching release string or exported-symbol CRC
is not enough on its own. See
[audio behavior and validation](docs/05-runtime-power-network.md#speaker-and-headphone-pop-repair).

## Documentation

The docs in `docs/` describe how BaseOS works now: its behavior, interfaces and
how to validate them. Keep experiment logs, before-and-after results and
debugging history out of them. Those belong with the experiment in
`experiments/`.

- [Boot chain and partitions](docs/00-boot-chain-and-partitions.md)
- [Root filesystem and init](docs/01-rootfs-and-init.md)
- [Image build and flashing](docs/02-image-build-and-flash.md)
- [First boot and storage expansion](docs/03-first-boot-and-expand.md)
- [Boot logo and exceptional status UI](docs/04-boot-splash.md)
- [Runtime, boot timing, power, and networking](docs/05-runtime-power-network.md)
- [Hardware validation and constraints](docs/06-status-and-lessons.md)
- [Partition layout and A/B system updates](docs/07-partition-layout-and-updates.md)
- [USB adb and H700 OTG](docs/08-usb-adb-and-otg.md)
- [Boot performance](docs/09-boot-profiling.md)
- [Boot SD-card I/O](docs/10-boot-io-audit.md)
- [Charger-only boot](docs/11-charger-only-boot.md)
- [Boot files](docs/12-kernel-gzip.md): the faster boot files every model uses,
  and the one-time migration inside a normal `.bosupd` that brings them to
  existing cards

The experiments hold the measurements behind the boot changes:

- [Boot-speed experiments](experiments/h700-boot-speed/README.md): the
  measurements behind each change, and the tools that took them
- [RG SP gzip experiment](experiments/rgsp-kernel-gzip/README.md): the original
  investigation
- [RG SP boot timing](experiments/rgsp-boot-timing/README.md): stage times from
  the hardware counter, and repeated comparisons of the gzip and original
  kernels, kept apart from LED-on and frontend measurements
