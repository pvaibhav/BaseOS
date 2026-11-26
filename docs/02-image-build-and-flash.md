# 02. Firmware preparation, image build and flash

Everything runs on macOS. Linux filesystem work happens inside unprivileged
Alpine containers. The build needs no sudo, no loop mounts and no running
handheld.

`tools/docker-platform.sh` selects the container platform:

- Steps that build or execute aarch64 device binaries (`build-tools.sh`,
  `build-rootfs.sh`) pin `linux/arm64`.
- Preparation, image packing, bootlogo generation, the QEMU userspace smoke
  test and most synthetic tests pin the host CPU (`linux/amd64` on Intel Macs,
  `linux/arm64` on Apple Silicon), so they run natively.

The QEMU test still boots an aarch64 guest, with a kernel fetched from Alpine's
aarch64 index. Only the emulator binary is host-native, which avoids nested
emulation on Intel.

## 1. Inputs and targets

Normal builds use the published prepared cache through `fetch-prepared.sh`.
Preparing new inputs requires an extracted stock or StockMod `.img`; BaseOS
does not download or unpack multipart vendor archives.

`devices.json` defines eleven target profiles, including `rgsp`. Run
`python3 tools/device_profile.py list` for the current IDs, or see the
[device table](../CONTRIBUTING.md#building). Profiles specify model identity,
filename matching, logo dimensions, rotation and radio capabilities.

Preparation validates GPT geometry and CRCs. Full stock and StockMod images
must have a matching backup GPT. StockMod BASE images may omit `primary`. Such
an image either ends exactly after p7 without a backup GPT, or keeps a valid
full-disk backup. Preparation rejects any other truncation. It normalizes
stock layouts to StockMod offsets, and image composition restores `primary`
when it is absent.

Preparation copies everything before p5 into `boot-prefix.img` and extracts the
allowlisted userspace from p5 with `debugfs`. A static BusyBox tar then runs
chrooted inside the extracted root, so it dereferences absolute and relative
symlinks safely within that root. A final GNU tar pass normalizes ordering,
owners and timestamps.

Preparation produces these files under `work/<target>/` for each target:

- `boot-prefix.img`: the raw boot region plus partitions 1–4.
- `stock-harvest.tar`: the deterministic, dereferenced stock userspace
  allowlist.
- `source.json`: target, model and capabilities; vendor filename, size and
  SHA-256; complete GPT geometry; output hashes; preserved partition hashes;
  and logo dimensions.

Paths in `manifest/harvest.list` are required unless they belong to the
WiFi/Bluetooth sections (or the matching kernel module) and the target profile
declares that radio absent. `source.json` records the permitted omissions. Any
other missing file fails preparation, so BaseOS never emits a rootfs that
silently lacks some function.

## 2. Build one specific target, end to end

First extract the StockMod download with 7-Zip. For a multipart download, open
or extract the `.7z.001` file; 7-Zip reads the following volumes on its own.
BaseOS does not unpack these archives itself. Confirm that extraction produced
one `.img` whose filename matches the target profile, or its configured
filename alias in `devices.json`.

From the repository root, substitute the target and extracted image path you
want:

```sh
TARGET=rg40xxv
FIRMWARE=/path/to/RG40XXV-...-mod-....img

./prepare-stock.sh "$TARGET" "$FIRMWARE"
./build-tools.sh                         # shared; rerun when src/ changes
./build-rootfs.sh "$TARGET"
./test-boot-qemu.sh "$TARGET"
./build-image.sh "$TARGET"
./build-update.sh "$TARGET"                # optional: the .bosupd update payload
```

The flashable result is `work/<target>/baseos-<target>.img`. Preparation and
build artifacts stay together in the same target directory:

- `source.json`: source identity, hash, GPT geometry and derived hashes.
- `boot-prefix.img` and `stock-harvest.tar`: prepared StockMod inputs.
- `rootfs.tar` and `closure-report.txt`: the assembled and checked userspace.
- `bootlogo.bmp` and `baseos-<target>.img`: the final target-specific outputs.
- `baseos-<target>-<version>.bosupd`: the update payload, if `build-update.sh`
  ran.

To list removable media and then flash the image on macOS:

```sh
./flash-card.sh "$TARGET"
./flash-card.sh "$TARGET" diskN
```

The second command asks you to type a confirmation, then destroys all data on
the selected disk. Use the whole disk identifier (`diskN`), never a partition
such as `diskNs1`.

`build-rootfs.sh` verifies the preparation hashes before assembling BusyBox,
the target harvest and the BaseOS overlay. It generates `/etc/baseos-release`
and the NextUI-compatible `/mnt/vendor/bin/dmenu.bin` model stub from
`devices.json`. `BASEOS_TARGET` identifies the exact hardware, `BASEOS_DEVICE`
is the frontend family, and `BASEOS_MODEL_STRING` is the stock-style
compatibility value.

`build-image.sh` reads the p2 and p5 offsets from `source.json`, not from
constants. It then:

- preserves p1 and p3 byte for byte
- derives the exact [boot files](12-kernel-gzip.md), including the one changed
  byte and checksum in boot0
- keeps the p2 geometry and replaces only `bootlogo.bmp`
- regenerates both GPTs
- packs `/usr/lib` into a
  [squashfs file](01-rootfs-and-init.md#the-usrlib-image) inside the rootfs
- creates journalled ext4 filesystems that the 4.9 kernel can mount
- creates the small FAT data partition that first boot expands

The build renders bootlogos per target at 480×640, 640×480, 720×480 or
720×720. At runtime, `fbsplash` still reads the actual framebuffer geometry.

## 3. One-command and batch builds

Put one `.img` for every model you want in a directory. With no target list,
the batch command requires every configured target. Target arguments select a
subset:

```sh
./build-stockmod.sh /path/to/firmware
./build-stockmod.sh /path/to/firmware rg40xxv
./build-stockmod.sh /path/to/firmware rg28xx rg40xxv rgcubexx
```

The command checks all matches before preparation starts. A missing image, a
duplicate target, or more than one matching `<StockMod-prefix>*.img` is an
error. The build compiles the shared static tools once or reuses them. Every
selected target then gets its own preparation, rootfs, QEMU smoke test, image
and verification artifacts. The one-target form is a short equivalent of the
complete recipe in section 2.

## 4. Image geometry and verification

The normalized p5 start becomes rootfs slot A. The environment selects
partition number 5, and GPT slot switching can move its extent to slot B.
BaseOS then packs these regions, plus headroom for the backup GPT:

- a 512 MiB rootfs slot
- an unallocated slot B of the same size
- a 128 MiB userdata filesystem
- an initial FAT32 filesystem of roughly 64 MiB

The normalized image is about 1.5 GB. Its compressed size depends on the
rootfs contents. `expand-storage` grows the FAT partition to fill the card on
first boot.

Every image build checks:

- preparation size and SHA-256 values, and target identity
- GPT CRCs, names, GUIDs, starts and non-overlap
- exactly seven partitions, with `UDISK` two slots past the rootfs start
- the hidden attribute bits on everything except the user-visible `primary`
- the raw boot region and p1, p3 and p4 against the prepared StockMod bytes
- rootfs slot A and `UDISK` with read-only `e2fsck`, and that slot B is empty
- `/init`, `gptgrow`, `gptslot`, executable `expand-storage` and
  `baseos-update`, and exact target identity in the rootfs
- the loop driver and LZ4 squashfs in the target's kernel
- the `/usr/lib` image against the directory it replaces, its copy inside slot
  A, and an empty `/usr/lib` directory in slot A
- the embedded bootlogo bytes and native dimensions
- FAT32 readability on `primary`

The synthetic importer suite covers valid preparation, deterministic output,
dereferenced relative and absolute symlinks, directories, invalid GPT magic,
incorrect primary or backup GPT data, incorrect partition names, truncation,
ambiguous firmware matches and required-file omissions:

```sh
./test-prepare-stock.sh
```

QEMU validates generic userspace plumbing, not the vendor kernel or hardware.
See [hardware validation](06-status-and-lessons.md) for device-specific
coverage.

## 5. Flash and optional device validation

```sh
./flash-card.sh rg40xxv             # list removable candidates
./flash-card.sh rg40xxv diskN       # typed confirmation, write, eject
./validate-on-device.sh rg40xxv <device-ip> # optional post-boot validation
```

The flasher refuses internal and synthesised disks, and disks smaller than the
selected image. Never flash the StockMod source card. On-device validation and
development SSH stay useful after boot, but preparation and the build do not
depend on them.
