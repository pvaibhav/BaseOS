# 00. Boot chain and partitions

The vendor bootloader and kernel come from each target's prepared firmware.
Preparation normalizes stock layouts to StockMod geometry. Image composition
replaces the p2 bootlogo, regenerates the GPT and derives the
[boot files](12-kernel-gzip.md). The boot files are:

- a gzip kernel
- one pinned U-Boot instruction that selects the existing gzip decoder
- an empty ramdisk in place of the vendor initramfs
- three kernel arguments
- serial output switched off in boot0 and U-Boot
- five USB host controllers marked for a later start

The unpacked kernel, the rest of the device trees and the environment stay
unchanged.

The geometry below is the normalized H700 layout. `source.json` records the
target-specific inputs and preserved-region hashes.

## 1. GPT partition layout

The card uses GPT. Preparation accepts full stock and StockMod images, plus
StockMod BASE images with the final `primary` entry omitted. A BASE image may
end at p7 without a backup GPT, or include a valid full-disk backup.
Preparation rejects any other truncation. Image composition writes both GPT
copies and restores the `primary` entry when it is absent.

BaseOS ships seven partitions, not the stock eight. It drops `appfs`, and the
region `appfs` occupied becomes the unallocated second rootfs slot (see
[07](07-partition-layout-and-updates.md)). Every partition except the
user-visible FAT volume has GPT attribute bits 62 and 63 set, which asks
Windows to assign a single drive letter. macOS can still expose the vendor FAT
volume; see
[desktop visibility](07-partition-layout-and-updates.md#desktop-visibility).

| # | name | start LBA | size (stock) | contents | our image |
|---|---|---|---|---|---|
| — | (gap) | 0–73727 | 36 MiB | protective MBR, primary GPT, boot0 and U-Boot blobs | regenerated GPT; U-Boot selector, serial output settings and checksums updated |
| 1 | `special` | 73728 | 64 MiB | vendor special (an almost-empty ext4) | verbatim, hidden |
| 2 | `boot-resource` | 204800 | 32 MiB | vfat: `bootlogo.bmp`, `fastbootlogo.bmp`, fonts, DTBs | verbatim except `bootlogo.bmp` ([04](04-boot-splash.md)), hidden |
| 3 | `env` | 270336 | 16 MiB | U-Boot environment (bootargs) | verbatim, hidden |
| 4 | `boot` | 303104 | 64 MiB | Android boot image: kernel and vendor initramfs | gzip kernel, empty ramdisk, kernel arguments, original DTB, hidden |
| 5 | `rootfs` | **434176 or 1482752** | 7 GiB stock | Ubuntu 22.04 (4.1 GB used) | the active 512 MiB slot, hidden |
| — | *(slot B)* | 1482752 or 434176 | — | — | **512 MiB unallocated, the update target** |
| 6 | `UDISK` | 2531328 | 512 MiB stock | stock scratch/swap | 128 MiB ext4 for `/data` persistent state, hidden |
| 7 | `primary` | 2793472 | rest | vfat ROMs partition | **64 MiB empty FAT32, grown to fill the card on first boot. The only visible volume.** |
| 8 | *(empty)* | — | — | — | — |

`UDISK` and `primary` keep the stock entries' names, type GUIDs and unique
GUIDs. BaseOS shifts them down one slot; it does not rename them.

### Boot constraints

- U-Boot builds the kernel cmdline's `partitions=` map from the GPT partition
  names. Hardware testing confirmed this: p3 stores `partitions=${partitions}`
  unexpanded, and `/proc/cmdline` shows it expanded to exactly the GPT names.
  `bootcmd` boots the partition named `boot` through `sunxi_flash`. Boot
  therefore depends on the partition names and their order.
- `root=/dev/mmcblk0p5` comes from `mmc_root` in the environment, which BaseOS
  never modifies. It names a partition number, not an address. Nothing in
  boot0, U-Boot or the environment references the start LBA of p5, so
  partition 5 can live anywhere. That is what lets BaseOS switch A/B slots by
  rewriting only the GPT.
- Nothing is known to depend on the partition type and unique GUIDs, because
  U-Boot matches by name. BaseOS preserves them anyway. Keeping them costs
  nothing and keeps the table as close to stock as possible. BaseOS sets the
  GPT attribute bits, and cards boot fine with them.
- `prepare-stock.sh` derives `boot-prefix.img` directly from the selected
  target's normalized firmware layout. The prefix ends exactly at the start of
  p5. On the known RG40XXV layout it is 222,298,112 bytes. For other targets
  the script reads the size from their GPT instead of assuming that value.
  `work/<target>/source.json` records the source name and hash, the geometry,
  the prefix hash and the preserved-region hashes. The image starts from this
  prefix, and the build then regenerates both GPTs for the smaller target size
  (see `tools/mkgpt.py` in [02](02-image-build-and-flash.md)).
- `bootdelay=0` is already set, so U-Boot has no key-press window to shorten.

### The env partition (p3) cmdline

Read raw from p3 (`dd if=/dev/mmcblk0p3 | strings`), the environment stores a
template, not a finished cmdline:

```
mmc_root=/dev/mmcblk0p5
setargs_mmc=setenv bootargs ... root=${mmc_root} rootwait quiet splash
            init=${init} partitions=${partitions} cma=${cma} snum=${snum} ... gpt=1
```

U-Boot fills in `partitions` at runtime from the GPT partition names. This is
why the names matter and the start LBAs do not. On the running handheld,
`/proc/cmdline` reads:

```
root=/dev/mmcblk0p5 rootwait quiet splash init=/init
partitions=special@mmcblk0p1:...:rootfs@mmcblk0p5:UDISK@mmcblk0p6:primary@mmcblk0p7
console=ttyS0,115200 loglevel=4 cma=64M ... gpt=1 lcd_type=boe
```

We never modify p3. U-Boot appends the command-line field of the boot image
header to these arguments, and BaseOS puts
`loglevel=1 rcupdate.rcu_expedited=1 rcupdate.rcu_normal_after_boot=1` there.
That `loglevel` comes last, so it overrides `quiet`. To see kernel messages on
the serial port while debugging, run `dmesg -n 7` after startup, or build a
boot image with a different header field.

## 2. The boot image and the vendor initramfs

Partition 4 (`boot`) holds an Android boot image with the `ANDROID!` magic and
2 KiB pages. It contains the kernel, then a ramdisk, then a device tree. The
vendor ramdisk is a small BusyBox 1.22 initramfs from 2015. Its `/init` does
four things.

1. It parses `root=`, `gpt=` and `console=` from `/proc/cmdline`.
2. It runs `e2fsck -y` on the root device.
3. It mounts root with
   `-o rw,noatime,nodiratime,norelatime,noauto_da_alloc,barrier=0,data=ordered`.
4. It runs `mount --move /dev` and `exec switch_root /mnt /init`.

BaseOS images replace that ramdisk with an empty archive. The kernel finds no
`/init` in it, mounts `root=/dev/mmcblk0p5` read-only and runs the `/init` of
the root filesystem. `rcS` then runs the filesystem check when one is due and
remounts the root writable with the same options. See
[boot files](12-kernel-gzip.md).

A card installed by release 1.3.0 or earlier boots through the vendor initramfs
until its first update migrates it. The root filesystem must therefore satisfy
both paths.

- The root ext4 must have a journal. The initramfs mounts with `data=ordered`,
  and on a filesystem without a journal the kernel rejects that option with
  `EXT4-fs: can't mount with data=, fs mounted w/o journal`. The device then
  stays on the boot splash. Do not pass `^has_journal` to `mke2fs` for p5.
- `/init` must be a regular file. The 2015 `switch_root` fails when `/init`
  is a symlink to `sbin/init`, which is itself a symlink to `busybox`. Stock's
  single symlink works, and nobody has established the exact difference. We
  ship `/init` as a script that runs BusyBox init. See
  [01](01-rootfs-and-init.md).
- The image needs static `/dev/console` and `/dev/null` nodes. Without the
  initramfs, `/dev` is the root filesystem's own directory until `rcS` mounts
  devtmpfs.

Both paths end with the root filesystem mounted read-write. We do not remount
it read-only. See the read-only item in [06](06-status-and-lessons.md).

## 3. ext4 feature policy for the 4.9 BSP kernel

The vendor kernel is 4.9.170. Modern `mke2fs` (e2fsprogs 1.47) enables features
this kernel cannot mount. The build creates both of our ext4 partitions, the
rootfs slots and `UDISK`, with:

```
-O ^metadata_csum,^metadata_csum_seed,^64bit,^orphan_file
```

The build keeps a journal and disables these optional features for
compatibility with the vendor BSP kernel. Keep this feature mask aligned with
`build-image.sh`. If you change it, test the resulting filesystem with the
target's vendor kernel.

`/proc/config.gz` on the device confirms these kernel config points:

- `CONFIG_DEVTMPFS=y` is set but `CONFIG_DEVTMPFS_MOUNT` is not. So `rcS`
  mounts devtmpfs itself, and the image includes static `/dev/console` and
  `/dev/null` nodes as a fallback.
- `CONFIG_FRAMEBUFFER_CONSOLE` is not set, so the kernel has no text console
  on the LCD. Use serial output or temporary instrumentation to diagnose boot.
- exfat, squashfs, overlay, f2fs and loop are built in. This 2026 kernel has
  native exfat.

## 4. Kernel modules

BaseOS uses these modules where the device profile supports the hardware.
Display, audio, input, PMIC, suspend and filesystem support come from the
vendor kernel's built-in drivers.

| module | role | notes |
|---|---|---|
| `mali_kbase.ko` | Mali-G31 GPU (`/dev/mali0`) | debug data stripped during the build; loaded in the background during `rcS` |
| `8821cs.ko` | RTL8821CS WiFi (SDIO) | WiFi firmware embedded in the module; loaded async |
| `rtl_btlpm.ko` | RTL8821C Bluetooth low-power handshake | loaded during early boot |

Bluetooth controller firmware lives at `/lib/firmware/rtlbt/` (`rtlbt_fw`,
`rtlbt_config`). `rtk_hciattach -n -s 115200 ttyS1 rtk_h5` loads it.

## 5. Regenerating the GPT for a different image size

`tools/mkgpt.py` reads the primary GPT from the copied `boot-prefix.img` and
then does the following:

- keeps every partition's name, type GUID and unique GUID, and the p1–p5 start
  offsets
- drops `appfs` and shifts `UDISK` and `primary` down one entry
- reserves the second rootfs slot as unallocated space
- sets the hidden attribute bits
- writes valid primary and backup GPTs with correct CRCs, plus a protective MBR
  covering the target size

It follows these conventions, which `gptgrow` matches exactly
([03](03-first-boot-and-expand.md)):

- 8 entries of 128 bytes each at LBA 2–3
- first usable LBA 4
- backup entries at `total-3..-2`
- backup header at `total-1`
- last usable LBA `total-4`

See [hardware coverage](06-status-and-lessons.md) for validation status.
