# Contributing to BaseOS

## Development environment

Builds run on macOS using unprivileged Alpine containers (Docker or OrbStack), so
the host does not need sudo or loop mounts. Steps that build or execute AArch64
device binaries pin `linux/arm64` (native on Apple Silicon and emulated on Intel).
Preparation, image composition, bootlogo generation, and QEMU userspace smoke
tests pin the host architecture (`linux/amd64` on Intel and `linux/arm64` on Apple
Silicon) so those steps stay native.

Building does **not** need the vendor firmware images. `./fetch-prepared.sh`
restores the prepared per-target inputs from one published bundle instead of a
~12 GB image per target. Firmware is only needed to add a device or to move a
target onto a new vendor release; both are covered below.

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

Both scripts take an optional target list, which is what you want while iterating:

```sh
./fetch-prepared.sh rg40xxv
./build-all.sh rg40xxv
./flash-card.sh rg40xxv diskN
```

`build-all.sh` is the existing per-target chain — rootfs, QEMU userspace smoke
test, image, update payload — plus packaging, and it rebuilds the shared tools
when required binaries are missing or the source stamp changes. The stamp
covers C/header files in `src/` and `tools/`, plus adb patches. Rerun
`build-tools.sh` explicitly after changing build recipes or pinned dependencies. Those steps remain individually runnable:

```sh
./build-tools.sh
./build-rootfs.sh rg40xxv
./test-boot-qemu.sh rg40xxv
./build-image.sh rg40xxv
./build-update.sh rg40xxv
```

The release version lives in the repo-root `VERSION` file; `build-rootfs.sh` bakes
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

Each target's profile lives in `devices.json`. `panel_rotation_ccw` is the angle the
splash is turned through to land upright on a panel that is mounted turned — `90` on the
RG28XX, whose 480×640 panel is held in landscape, and `0` everywhere else. See
[docs/04-boot-splash.md](docs/04-boot-splash.md) §2.1 before changing it: the direction
is fixed by the vendor bootlogo, not by taste.

## The prepared-artifact cache

`prepare-stock.sh` derives exactly three things per target from a vendor image:
`boot-prefix.img`, `stock-harvest.tar` and `source.json`. The cache ships the first
two pre-made; the third is committed:

```
manifest/prepared/<target>.json   each target's source.json — the trust anchor
manifest/prepared/bundle.sha256   hash of the published bundle
manifest/prepared/bundle.url      where that bundle lives
```

Integrity is checked twice and both anchors are in git. `bundle.sha256` covers the
download; the size and SHA-256 of each artifact, recorded in the committed
`source.json`, cover the contents — and that second check is the one
`build-rootfs.sh` and `build-image.sh` already run on every build. A bad restore
therefore fails exactly where a bad preparation would.

The cache is an optimisation, never a source of truth: `prepare-stock.sh`
regenerates the same artifacts from the vendor image and must produce the same
hashes. `./fetch-prepared.sh --from <bundle>` uses a local bundle instead of
downloading, and refuses to overwrite a target you prepared locally from different
firmware unless you pass `--force`.

## Stock and StockMod firmware

`prepare-stock.sh` accepts either an Anbernic **stock** image or a **StockMod**
one, and produces the same output from both — one build path, one image format.
Which it is comes from the partition table, never from the filename:

| | partition 1 | p2–p7 |
|---|---|---|
| StockMod | `special`, 64 MiB empty ext4 | start at 204800 |
| stock | `Roms`, 2 GiB user-visible FAT32 | start ~1.94 GiB higher |

StockMod BASE images may omit the final `primary` partition, either in a trimmed
image or a full-disk image with a valid backup GPT, as in StockMod v4.

A stock table is recast into the StockMod one before anything else runs: `Roms`
is dropped and rebuilt as an empty 64 MiB `special`, and everything after it
moves down by the difference — which lands p2–p5 on exactly the StockMod
offsets. `boot0`, U-Boot, `env` and `boot` are carried over untouched.

This is safe because **StockMod performs the same re-partition and boots.** With
an RG34XXSP stock and StockMod image side by side, `env` and `boot` are
byte-identical across the move and U-Boot is unmodified, because it resolves
`partitions=` from GPT names and `root=` by partition number rather than by
address ([boot contract](docs/07-partition-layout-and-updates.md#1-boot-contract)). StockMod's only other boot-chain change is one nibble of
`dram_para[28]` plus its checksum — a DRAM trim, not something the move needs,
so a stock-derived card keeps its own timings.

`source.json` records `source_layout` (`stock` or `stockmod`), keeps the
original vendor table under `source.partitions`, and reports the normalised
table as `layout`. Preparing from stock needs the whole card, because the rootfs
harvest is read from partition 5; a truncated stock image is refused with that
reason.

## Adding a device model

The only workflow that still needs a vendor `.img` — once, for one person.

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
GPIO and ASoC layouts before updating that profile; a matching release string
or exported-symbol CRC alone is insufficient. See
[audio behavior and validation](docs/05-runtime-power-network.md#speaker-and-headphone-pop-repair).

## Tracking a new vendor firmware

Each target pins a specific vendor firmware through its committed `source.json`, so
a new Anbernic release changes nothing until you choose to take it — per target.

```sh
./prepare-stock.sh rg40xxv /path/to/RG40XXV-<new>.img
git diff manifest/prepared/rg40xxv.json    # exactly what moved
./verify-target.sh rg40xxv
./cache-pack.sh
./build-all.sh rg40xxv && ./validate-on-device.sh rg40xxv DEVICE_IP ROOT_PASSWORD
```

The diff is the review: partition geometry, artifact hashes and the vendor image's
own provenance all change visibly in one small file.

`verify-target.sh` gates the assumptions a refreshed firmware could silently
invalidate — the recorded partition layout, that p1 `special` is an empty ext4,
that p3 `env` still boots `/dev/mmcblk0p5` and still leaves `partitions=` for
U-Boot to synthesise from GPT names, and that the p4 kernel still exports every
symbol the harvested modules import with matching modversions CRCs. That last one
matters most: a refreshed kernel whose exports have moved ships modules that fail
to `insmod`, leaving a device with a dead GPU or no Wi-Fi and no other symptom.
`tools/kernel_abi.py` recovers the export table straight out of the vendor `Image`
(see its module docstring for how, given there is no `vmlinux` or `Module.symvers`)
and can also check one target's modules against another's kernel:

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
python3 tests/test-kernel-gzip.py  # requires all prepared firmware inputs
./tests/test-wifi-init.sh
./tests/test-systemctl-wifi.sh
./tests/test-poweroff-policy.sh
./tests/test-axp-off.sh
./tests/test-baseos-config.sh
./tests/test-baseos-mdns.sh
python3 tests/test-strip-gpu-module.py
./tests/test-splash-rotation.sh
./tests/test-baseos-ntp.sh
./tests/test-timedatectl.sh
./tests/test-rtc-utc-policy.sh
./tests/test-gpt-slot.sh
./tests/test-update-apply.sh
./tests/test-boot-menu-held.sh
./tests/test-usb-gadget-adb.sh
./tests/test-usb-gadget-watch.sh
./tests/test-usb-storage-mode.sh
./tests/test-axp-off.sh
./tests/test-power-intent.sh
./tests/test-poweroff-policy.sh
./test-boot-qemu.sh rg40xxv
./test-update-roundtrip.sh rg40xxv   # needs build-image.sh + build-update.sh first
./validate-on-device.sh rg40xxv DEVICE_IP ROOT_PASSWORD
```

## Boot-performance changes

The README distinguishes BaseOS handoff timing from frontend startup time.
Update timing figures only after repeated measurements show a meaningful change;
label kernel-relative timings separately from power-on measurements. Developer
docs should describe current behavior, interfaces and validation requirements;
keep experiment logs, old/new results and debugging history out of them.

The kernel-to-frontend handoff ceiling is 2.50 seconds for all devices, enforced by
`validate-on-device.sh`. See [boot performance](docs/09-boot-profiling.md) for
measurement scope, comparison procedure and GPU module optimization.

## Technical documentation

The [gzip boot-pair implementation](docs/12-kernel-gzip.md) covers every supported
target, including one-time migration through a normal `.bosupd`. Its benefit
appears in the pre-kernel and combined timings, outside the existing
kernel-to-frontend budget. The original
[RG SP experiment](experiments/rgsp-kernel-gzip/README.md) retains investigation evidence.
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
