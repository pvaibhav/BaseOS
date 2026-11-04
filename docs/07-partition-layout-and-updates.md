# 07. Partition layout and A/B system updates

BaseOS uses seven GPT partitions and two rootfs slots. An update writes the
inactive slot, verifies it, then changes partition 5 to select that slot.
Userdata and frontend storage keep their existing extents.

## 1. Boot contract

U-Boot loads the partition named `boot`, and the kernel mounts partition number
5 as root. The kernel takes that partition's start address from GPT, so a slot
switch needs no change to the vendor bootloader or kernel. Partition names and
order must stay compatible with the vendor boot chain.

## 2. Layout

The normalized layout uses these LBAs. Builds derive them from the prepared
source geometry and the configured slot sizes.

| Entry | Name | Start LBA | Size/role |
| --- | --- | --- | --- |
| 1 | `special` | 73728 | 64 MiB vendor partition |
| 2 | `boot-resource` | 204800 | 32 MiB vendor FAT, with BaseOS bootlogo |
| 3 | `env` | 270336 | 16 MiB vendor environment |
| 4 | `boot` | 303104 | 64 MiB vendor Android boot image |
| 5 | `rootfs` | 434176 or 1482752 | Active 512 MiB slot |
| — | Inactive slot | 1482752 or 434176 | 512 MiB unallocated extent |
| 6 | `UDISK` | 2531328 | 128 MiB persistent `/data` |
| 7 | `primary` | 2793472 | FAT32 frontend storage, expanded on first boot |

Entry 8 is empty. The layout omits `appfs`. `UDISK` and `primary` keep their
vendor identities when the prepared input has them. The initial image is
2,926,592 sectors (1,498,415,104 bytes). Its compressed size depends on the
contents.

### Desktop visibility

All entries except `primary` carry attributes `0xC000000000000000`: hidden
(bit 62) and no drive letter (bit 63). `primary` stays Microsoft Basic Data
with attributes zero. This asks Windows to assign one drive letter. Check what
the host actually does during hardware acceptance. macOS can still mount the
vendor FAT `boot-resource` volume because it ignores those attributes.

## 3. Update payload

A `.bosupd` is an uncompressed ustar archive that holds `manifest` followed by
`rootfs.img.gz`. The manifest contains:

```text
format=baseos-update/1
target=<device-id>
version=<release-version>
build=<build-id>
slot-sectors=1048576
image-size=536870912
image-sha256=<sha256 of decompressed rootfs image>
```

The rootfs bytes come from the image that `build-image.sh` builds. The manifest
hash checks the integrity of the decompressed image. Payloads are not signed.
`build-all.sh` also prints download-file checksums for the release page.

The updater replaces only the rootfs and leaves the p2 artwork alone. A rootfs
carries the [one-time boot-file migration](12-kernel-gzip.md). On its first
boot, the new system replaces the boot package and boot image, quiets boot0,
starts the frontend and confirms the trial. The next ordinary boot uses the new
files. A/B rollback does not cover those shared boot areas.

## 4. Selection and application

`baseos-update apply` runs after the cards are mounted. It scans the active
frontend card first, then TF1's visible partition when that is a different
volume. USB-storage mode skips scanning, including manual `apply` and `status`
calls.

The updater selects the first applicable payload in directory and glob order.
A payload must match the target and slot geometry, and must be newer than the
running version or the same version with a different build. Its image hash must
not already appear in `/data/update/history`. Selection does not jump straight
to the highest version. `baseos-update status` reports whether each candidate
is eligible.

The updater then:

1. validates the manifest and slot geometry;
2. decompresses into the inactive slot in chunks, updating progress as writes
   finish;
3. reads the slot back and verifies its SHA-256;
4. flips GPT partition 5 to the verified slot;
5. appends `<sha> <version> <build>` to `/data/update/history`, writes trial
   state to `/data/update/state` and flushes;
6. deletes the applied payload, flushes and reboots. If the payload volume is
   read-only, the updater remounts it writable for the delete, then restores it
   to read-only.

If a write or verification fails before the GPT flip, the active slot stays
selected. The inactive slot and diagnostic logs may already have changed, so
the card as a whole is not byte-identical to its state before the update. The
running kernel keeps its cached active-slot offsets until reboot.

The updater deletes only the payload it applied. Skipped or rejected files stay
on the card. Cleanup failures are logged and do not block the reboot.

An older updater could leave its archive behind. When upgrading from one,
confirmation also removes archives that match the current trial's exact image
hash, target, version and build. This one-time cleanup does not scan on later
confirmed boots and does not delete unrelated skipped payloads.

The updater records history at commit, so a payload that was left behind or
copied back is not reapplied after rollback. It reads the old `committed-sha`
file only to seed history during migration. Diagnostics, including
confirmation, go to `baseos-boot.log`; see [boot I/O](10-boot-io-audit.md).

## 5. Trial and rollback

`rcS` calls `baseos-update boot-check` before starting the frontend. The check
increments an open trial's attempt count and flips back on the third boot that
reaches it without confirmation. `frontend-session` confirms the trial when the
session starts, even with no frontend installed, and then removes the trial
state. It does not confirm when the dynamic loader is missing, because that
means the [`/usr/lib` image](01-rootfs-and-init.md#the-usrlib-image) did not
mount. Charging-only sessions reach neither step.

If the screen stays blank after the updater's restart, press reset. By then the
new slot is already written and flipped, so the reset starts it as a normal
trial boot. An RG40XX V connected to a computer does this on every release so
far; see [status](06-status-and-lessons.md#4-open-work).

Rollback works only if userspace reaches `boot-check`. It cannot recover a
rootfs that fails before that point. When the system can boot, switch slots by
hand with `gptslot /dev/mmcblk0 flip`.

`gptslot` derives both slot extents from GPT and refuses incompatible layouts.
It writes the backup entries and header and fsyncs them, then writes the
primary entries and header and fsyncs again. The sequence is not atomic. Power
loss during the primary GPT write can still leave inconsistent metadata.

## 6. Build and validation

```sh
./build-image.sh rg40xxv
./build-update.sh rg40xxv
```

The payload filename uses `VERSION`, and the build identity comes from
`git describe --always --dirty`. The builder checks both values against the
rootfs before packaging.

- `tests/test-gpt-slot.sh`: layout, slot arithmetic, flips and invalid geometry.
- `tests/test-update-apply.sh`: selection, rejection, integrity and trial behavior.
- `test-update-roundtrip.sh <target>`: real payload application to a copied image.

Hardware validation covers RG40XX V slot switching, `/data` persistence and
trial confirmation. Failed-trial rollback and Windows volume visibility need
separate hardware checks; see [validation coverage](06-status-and-lessons.md).
