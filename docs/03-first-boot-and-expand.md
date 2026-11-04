# 03. First boot: expand-to-fill, then add a frontend

The image ships a 64 MiB empty data partition (p7) and no frontend. On the
first normal boot, p7 grows to fill the card and receives `README.txt` and a
commented `baseos.conf` example. The user then copies a frontend onto it.
Later boots check the geometry and skip reformatting.

## 1. Why reformat instead of resize-in-place

Growing FAT32 in place would need `fatresize`, which pulls in libparted and has
no viable static build, or a hand-written cluster-relocation defrag, which is
complex and prone to corruption. Base OS bakes no payload, so a much simpler
path is safe:

- ship p7 small and empty;
- on first boot, grow the FAT partition's GPT entry to fill the card and write a
  fresh, empty FAT32 at full size.

Reformatting an empty partition can't lose data, so this needs no staging. It
also keeps the flashable image small, because p7 is small in the image. BusyBox
already ships `mkfs.vfat`, `partprobe` and `blockdev`, so no extra tools are
needed.

## 2. `gptgrow`: GPT growth and kernel live-resize (`tools/gptgrow.c`)

`gptgrow /dev/mmcblk0` is a static C tool with no dependencies. It:

1. reads the primary GPT and finds the highest-numbered non-empty partition (p7);
2. exits with code 1 if that partition already reaches `last_usable`, so a
   repeat run does nothing;
3. otherwise sets its ending LBA to `total-4`, rewrites both GPT headers, both
   entry tables (with inline CRC32) and the protective MBR, and calls `fsync`;
4. calls `ioctl(fd, BLKPG, BLKPG_RESIZE_PARTITION)` to resize p7 in the running
   kernel, then exits with code 0.

Step 4 is essential. A full partition-table reread (`partprobe`) fails with
EBUSY because the rootfs (p5) is mounted on the same disk.
`BLKPG_RESIZE_PARTITION` updates only p7's size in the kernel while its siblings
stay mounted, so `/dev/mmcblk0p7` shows the new size immediately without a
reread. Against a plain file, the ioctl fails with `ENOTTY` and is skipped, but
the tool still rewrites the GPT. That is the offline test path.

`test-expand-storage.sh` covers GPT growth and repeat runs on synthetic images.
See [hardware coverage](06-status-and-lessons.md) for device checks.

## 3. `expand-storage`: the first-boot orchestrator (`overlay/usr/sbin/expand-storage`)

`rcS` runs it before mounting the card. It:

1. runs `gptgrow /dev/mmcblk0` to grow the GPT and call BLKPG. The exit code
   decides whether to continue:
   - exit 1 means p7 already fills the disk, so the card is already set up. The
     script exits without logging, touching p7 or showing an expansion
     message, because this is the result on every normal boot. A frontend the user has copied on is never reformatted.
   - exit 2 means a GPT or device error. The script logs the failure and skips
     formatting, because a write error can happen after partial GPT changes.
   - exit 0 means p7 was just grown, so the script continues.
2. paints `baseos-splash --important 45 "EXPANDING STORAGE"` now that a real
   resize is confirmed;
3. runs `partprobe` or `blockdev --rereadpt` as a fallback. These return a
   harmless EBUSY if BLKPG already resized p7;
4. runs `mkfs.vfat -F 32 -n BASEOS /dev/mmcblk0p7` to create a fresh, empty,
   full-size FAT32;
5. mounts p7, copies `README.txt` and the commented `baseos.conf` example from
   `/usr/share/baseos/`, runs `sync`, and unmounts.

Before the frontend card is mounted, the script logs to `/data/baseos-boot.log`,
which persists on p6. Once the card is writable, rcS appends that buffer to
`/mnt/sdcard/baseos-boot.log` and removes the buffer. If mounting fails, the
buffer stays for recovery after power-off.

The script decides whether to format by checking whether p7 already fills the
disk, not by a `/data` flag or a content check. This keeps normal boots from
reformatting existing frontend content. If formatting fails after GPT growth,
the next geometry check still reports that p7 fills the disk, so automatic
formatting is not retried.

## 4. Adding a frontend (the hand-off)

BaseOS ships no frontend. After expansion the card holds only setup files, and
`frontend-session` shows `ADD FRONTEND TO SD CARD` and waits while init respawns
it. The user then:

1. mounts the card on a computer, where it shows the full-capacity `BASEOS`
   volume with `README.txt` and `baseos.conf`;
2. copies a frontend onto it. For NextUI, that is `MinUI.zip` and any
   `nextui.*.pakz` files;
3. reboots the handheld.

On the next normal boot, `frontend-session` extracts `.tmp_update/h700.sh` from
`MinUI.zip` when needed and runs that frontend installer. Pending `*.pakz` files
also trigger it. BaseOS shows a static installation or update pill; see
[boot splash](04-boot-splash.md).

Slot needs no installer. It runs an extracted `System/slot` binary instead.
See [frontend entry points](01-rootfs-and-init.md#4-frontend-session) for launch
selection and the [README](../README.md#installation-and-updates) for card setup.

## 5. Boot-to-boot behavior

| boot | expand-storage | frontend | net |
|---|---|---|---|
| 1st normal boot | grows and formats p7, copies setup files | add-frontend prompt unless TF2 has a frontend | expand, then select frontend |
| after user copies a frontend | geometry check, no format | NextUI installer or direct Slot launch | frontend-dependent |
| every later boot | no-op | `MinUI.zip`/pakz consumed → launch only | a few seconds to the frontend |

## 6. Edge cases and caveats

- If the card is exactly the image size, `gptgrow` does nothing and the initial
  FAT filesystem stays at roughly 64 MiB. The expansion path does not copy setup
  files in this case.
- First boot reformats p7, so the user must add the frontend after the first
  boot, not before. The expansion erases anything copied onto the small empty p7
  before that boot. This matches the usual handheld flow: flash, boot to expand,
  then add content.
