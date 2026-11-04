# Contributing to BaseOS

## Development environment

Builds run on macOS using unprivileged Alpine containers (Docker or OrbStack), so
the host does not need sudo or loop mounts. Steps that build or execute AArch64
device binaries pin `linux/arm64` (native on Apple Silicon and emulated on Intel).
Preparation, image composition, bootlogo generation, and QEMU userspace smoke
tests pin the host architecture (`linux/amd64` on Intel and `linux/arm64` on Apple
Silicon) so those steps stay native.

Building does not need the vendor firmware images. `./fetch-prepared.sh`
restores the prepared per-target inputs from one published bundle instead of a
~12 GB image per target. You need firmware only to add a device or to move a
target onto a new vendor release. Both are covered below.

## Building

```sh
./fetch-prepared.sh
./build-all.sh
```

That produces, for every model, the two files a release publishes:

```
work/<target>/baseos-<target>-<version>.img.zip   flash once
work/<target>/baseos-<target>-<version>.bosupd    every update after that
```

Both scripts take an optional target list, which is useful while iterating:

```sh
./fetch-prepared.sh rg40xxv
./build-all.sh rg40xxv
./flash-card.sh rg40xxv diskN
```

`build-all.sh` runs the per-target chain of rootfs, QEMU userspace smoke test,
image and update payload, then packages the results. It rebuilds the shared
tools when required binaries are missing or the source stamp changes. The stamp
covers C and header files in `src/` and `tools/`, plus adb patches. Rerun
`build-tools.sh` explicitly after changing build recipes or pinned dependencies.
You can still run each step on its own:

```sh
./build-tools.sh
./build-rootfs.sh rg40xxv
./test-boot-qemu.sh rg40xxv
./build-image.sh rg40xxv
./build-update.sh rg40xxv
```

The release version lives in the repo-root `VERSION` file. `build-rootfs.sh` bakes
it and `git describe` into `/etc/baseos-release` and `/etc/os-release`.

Target IDs are:

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

Each target's profile lives in `devices.json`. `panel_rotation_ccw` is the angle that
turns the splash upright on a panel mounted at a rotation. It is `90` on the RG28XX,
whose 480×640 panel is held in landscape, and `0` everywhere else. See
[docs/04-boot-splash.md](docs/04-boot-splash.md) §2.1 before changing it. The vendor
bootlogo fixes the direction, so it is not a matter of preference.

## The prepared-artifact cache

`prepare-stock.sh` derives exactly three things per target from a vendor image:
`boot-prefix.img`, `stock-harvest.tar` and `source.json`. The cache ships the first
two pre-made, and git tracks the third:

```
manifest/prepared/<target>.json   each target's source.json — the trust anchor
manifest/prepared/bundle.sha256   hash of the published bundle
manifest/prepared/bundle.url      where that bundle lives
```

Two checks protect integrity, and git holds both anchors. `bundle.sha256` covers the
download. The size and SHA-256 of each artifact, recorded in the committed
`source.json`, cover the contents. `build-rootfs.sh` and `build-image.sh` already
run that second check on every build, so a bad restore fails exactly where a bad
preparation would.

The cache is an optimisation, never a source of truth. `prepare-stock.sh`
regenerates the same artifacts from the vendor image and must produce the same
hashes. `./fetch-prepared.sh --from <bundle>` uses a local bundle instead of
downloading, and refuses to overwrite a target you prepared locally from different
firmware unless you pass `--force`.

## Stock and StockMod firmware

`prepare-stock.sh` accepts either an Anbernic stock image or a StockMod one and
produces the same output from both, so there is one build path and one image
format. It identifies the image type from the partition table, never from the
filename:

| | partition 1 | p2–p7 |
|---|---|---|
| StockMod | `special`, 64 MiB empty ext4 | start at 204800 |
| stock | `Roms`, 2 GiB user-visible FAT32 | start ~1.94 GiB higher |

StockMod BASE images may omit the final `primary` partition, either in a trimmed
image or a full-disk image with a valid backup GPT, as in StockMod v4.

Before anything else runs, `prepare-stock.sh` recasts a stock table into the
StockMod one. It drops `Roms`, rebuilds it as an empty 64 MiB `special`, and
moves everything after it down by the difference. That lands p2–p5 on exactly
the StockMod offsets. `boot0`, U-Boot, `env` and `boot` carry over untouched.

This is safe because **StockMod performs the same re-partition and boots.** With
an RG34XXSP stock and StockMod image side by side, `env` and `boot` are
byte-identical across the move and U-Boot is unmodified. This works because
U-Boot resolves `partitions=` from GPT names and `root=` by partition number
rather than by address
([boot contract](docs/07-partition-layout-and-updates.md#1-boot-contract)).
StockMod's only other boot-chain change is one nibble of `dram_para[28]` plus its
checksum. That is a DRAM trim the move doesn't need, so a stock-derived card
keeps its own timings.

`source.json` records `source_layout` (`stock` or `stockmod`), keeps the
original vendor table under `source.partitions`, and reports the normalised
table as `layout`. Preparing from stock needs the whole card, because
`prepare-stock.sh` reads the rootfs harvest from partition 5. It refuses a
truncated stock image and gives that reason.

## Adding a device model

This is the only workflow that still needs a vendor `.img`, and only once, by one
person.

```sh
# add the devices.json entry first: id, model, stockmod_prefix, model_string,
# bootlogo_width/height, panel_rotation_ccw
./prepare-stock.sh rgsp /path/to/RGSP-....img
./verify-target.sh rgsp
./cache-pack.sh
./build-all.sh rgsp && ./validate-on-device.sh rgsp DEVICE_IP ROOT_PASSWORD
```

Set the optional `filename_alias` only if the image arrives hand-named rather
than as a `<MODEL>-...` vendor release; `stockmod_prefix` covers the usual case.

`cache-pack.sh` runs `verify-target.sh` over every target before packing, writes
the bundle to `work/prepared/`, and prints the `gh release create` and `git add`
commands to finish with. Artifact releases are tagged by date, `prepared-YYYYMMDD`.

## Audio module builds

`build-rootfs.sh` runs `build-audio-module.sh <target>` and packages the resulting
speaker/headphone repair module. The build uses a pinned kernel source archive
and compiler, the target's embedded kernel configuration, and recovered vendor
symbol versions. Its cache is separate from `work/tools` and invalidates when
module sources, build inputs or the reviewed profile change.

The exact kernel hashes and private-symbol offsets live in
`src/h700-speaker-amp/profiles.json`. A firmware refresh must verify the codec,
GPIO and ASoC layouts before updating that profile. A matching release string
or exported-symbol CRC alone is not enough. See
[audio behavior and validation](docs/05-runtime-power-network.md#speaker-and-headphone-pop-repair).

## Tracking a new vendor firmware

Each target pins a specific vendor firmware through its committed `source.json`, so
a new Anbernic release changes nothing until you choose to take it for that target.

```sh
./prepare-stock.sh rg40xxv /path/to/RG40XXV-<new>.img
git diff manifest/prepared/rg40xxv.json    # exactly what moved
./verify-target.sh rg40xxv
./cache-pack.sh
./build-all.sh rg40xxv && ./validate-on-device.sh rg40xxv DEVICE_IP ROOT_PASSWORD
```

Review that diff. Partition geometry, artifact hashes and the vendor image's own
provenance all change visibly in one small file.

`verify-target.sh` checks the assumptions a refreshed firmware could silently
break:

- The partition layout matches the recorded one.
- p1 `special` is an empty ext4.
- p3 `env` still boots `/dev/mmcblk0p5` and still leaves `partitions=` for U-Boot
  to synthesise from GPT names.
- The p4 kernel still exports every symbol the harvested modules import, with
  matching modversions CRCs.

The last check matters most. If a refreshed kernel's exports have moved, the
harvested modules fail to `insmod`, which leaves a device with a dead GPU or no
Wi-Fi and no other symptom. `tools/kernel_abi.py` recovers the export table
straight out of the vendor `Image`, even though there is no `vmlinux` or
`Module.symvers`. Its module docstring explains how. It can also check one
target's modules against another's kernel:

```sh
python3 tools/kernel_abi.py check rg34xxsp --modules-from rg40xxv
```

To prepare and build from firmware in one command, without the cache:

```sh
./build-stockmod.sh /path/to/firmware rg40xxv
```

## Testing

Run checks relevant to the files changed. The main test entry points are:

```sh
./test-prepare-stock.sh
python3 tests/test-verify-harvest.py
python3 tests/test-prepared-cache.py
./test-expand-storage.sh
./tests/test-baseos-config.sh
./tests/test-h700-speaker-amp-load.sh
python3 tests/test-audio-gating.py
python3 tests/test-audio-module.py
./tests/test-baseos-mdns.sh
./tests/test-boot-splash-policy.sh
./tests/test-frontend-session.sh
./tests/test-boot-migration.sh
./tests/test-root-mount.sh
./tests/test-usr-lib-image.sh
./tests/test-input-dir-time.sh
python3 tests/test-kernel-gzip.py  # requires all prepared firmware inputs
python3 tests/test-late-usb.py     # requires all prepared firmware inputs
python3 tests/test-lib-image.py    # requires all prepared firmware inputs
./tests/test-wifi-init.sh
./tests/test-systemctl-wifi.sh
./tests/test-poweroff-policy.sh
./tests/test-axp-off.sh
python3 tests/test-strip-gpu-module.py
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
./test-boot-qemu.sh rg40xxv
./test-update-roundtrip.sh rg40xxv   # needs build-image.sh + build-update.sh first
./validate-on-device.sh rg40xxv DEVICE_IP ROOT_PASSWORD
```

## Boot-performance changes

The README distinguishes BaseOS handoff timing from frontend startup time.
Update timing figures only after repeated measurements show a meaningful change.
Label kernel-relative timings separately from power-on measurements. Developer
docs should describe current behavior, interfaces and validation requirements;
keep experiment logs, old/new results and debugging history out of them.

`validate-on-device.sh` enforces a kernel-to-frontend handoff ceiling of 2.50
seconds on all devices. See [boot performance](docs/09-boot-profiling.md) for
measurement scope, comparison procedure and GPU module optimization.

## Technical documentation

The [boot files](docs/12-kernel-gzip.md) cover every supported target and reach
existing cards through a one-time migration inside a normal `.bosupd`. They
shorten both the time before Linux and the kernel-to-frontend time. The
[boot-speed experiments](experiments/h700-boot-speed/README.md) hold the
measurements behind each change and the tools that took them. The original
[RG SP gzip experiment](experiments/rgsp-kernel-gzip/README.md) keeps its investigation evidence.
The [RG SP boot-timing follow-up](experiments/rgsp-boot-timing/README.md) records
counter-based stages and the repeated gzip/original-kernel comparison separately
from LED-on and frontend rendering measurements.

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
