# 12 — Boot files and the one-update migration

BaseOS runs the vendor's boot0, U-Boot and kernel code unchanged and alters
only their inputs. Every supported H700 image ships the same seven changes.
The build derives them per target from pinned vendor firmware.
`manifest/kernel-gzip.json` pins each target's original boot0, boot package,
U-Boot and boot partition by SHA-256. The builder refuses firmware it does not
recognise. A new vendor build needs a new audit and a new profile.

## What changes

| Where | Change | Effect |
| --- | --- | --- |
| U-Boot, inside the boot package | One Thumb instruction selects gzip for Android images. | U-Boot can unpack a gzip kernel. |
| Boot image, kernel | The kernel is stored as gzip level 9 with a fixed timestamp. | The kernel takes 7 MiB on the card instead of 17 MiB, so U-Boot reads less. The unpacked kernel is byte-identical. |
| Boot image, ramdisk | A 512-byte empty archive replaces the vendor initramfs. | The kernel mounts the root partition itself. `rcS` does what the initramfs did. |
| Boot image, header command line | The field holds `loglevel=1 rcupdate.rcu_expedited=1 rcupdate.rcu_normal_after_boot=1`. | U-Boot appends the field to the kernel arguments. The kernel prints only panics to the serial port. RCU grace periods take microseconds while the kernel starts and return to normal afterwards. |
| Boot package, device tree | `debug_mode` in `/soc/platform` changes from 3 to 0. | U-Boot prints nothing to the serial port. |
| boot0 header | The `debug_mode` byte changes from 3 to 0. | boot0 prints nothing to the serial port. |
| Boot package, device tree | The status of five USB host controllers changes from `okay` to `late`. | The kernel does not start them before init. A module starts them during startup. |

The rest of U-Boot, the monitor, the hardware description in the device trees,
the environment partition and the kernel stay as the vendor shipped them. The
serial port works as before once the system is up, and `dmesg` still holds
every kernel message.

### What the initramfs did

The vendor initramfs ran `e2fsck -y` on the root partition, mounted it with
`rw,noatime,nodiratime,noauto_da_alloc,barrier=0,data=ordered` and switched to
it. Without the initramfs the kernel replays the journal and mounts the
partition read-only with default options. `rcS` then does two things.

1. It runs the bundled static `e2fsck -y` if the filesystem has recorded
   errors. The count is in `/sys/fs/ext4/mmcblk0p5/errors_count`.
2. It remounts the root with `rw,noatime,nodiratime,nobarrier,noauto_da_alloc`.
   If `e2fsck` changed anything, `rcS` leaves the root read-only until the
   next restart, because the kernel may still hold the old contents in memory.

A card that still boots through the initramfs hands `rcS` a writable root, and
`rcS` skips both steps. One rootfs therefore works with all three boot states
described below.

### The USB host controllers

The vendor kernel starts each USB host controller in turn before it runs init.
An EHCI controller takes about 30 ms and an OHCI controller about 70 ms, and
the six of them together take 0.3 s. The boot files mark EHCI 1, OHCI 1,
OHCI 2, EHCI 3 and OHCI 3 with the status `late`. The kernel treats any status
other than `okay` as unavailable and skips those five.

`rcS` loads `h700_late_usb.ko` in the background right after the GPU module.
The module looks for host controller nodes whose status is exactly `late`,
gives each one the status `okay` and creates its platform device under the
same parent as before. The built-in vendor drivers then start the controller
as they would have during kernel start-up, and its sysfs path is unchanged. On
the RG SP all six controllers are running 0.4 s after init starts.

EHCI 2 keeps the status `okay`. With every controller of ports 2 and 3 off,
the OTG port did not enumerate as a USB device and adb over USB stopped
working. With EHCI 2 left on, it worked.

Two things differ from the vendor boot. USB bus numbers change, because EHCI 2
now registers first and becomes `usb1`. Each port's EHCI controller still
registers before its OHCI controller. And a rootfs without the module leaves
the five controllers off, which matters only for the case described under
[migration](#migration-of-existing-cards).

The module calls only exported kernel functions. The build compiles it for
each target's kernel and checks every imported symbol version against that
kernel, as it does for the speaker module.

### A side effect on `/dev/input`

The kernel clock counts from zero until the RTC driver sets it, and the input
devices register before that. With the vendor boot files the last of them
registered in the second second, so `/dev/input` had a modification time of 1.
With the new files it registers around the one-second mark, and in about a
third of boots the time is 0. SDL's gamepad detection without udev starts from
0 and scans the directory only when its time differs, so a program using it
found no gamepad on those boots.

`rcS` sets the time to 1 right after it mounts `/dev`, whichever boot files
are installed. [The experiment notes](../experiments/h700-boot-speed/README.md#the-gamepad-that-sdl-could-not-find)
have the measurements.

## Build contract

`tools/kernel_gzip.py` derives three pieces from the prepared inputs. For each
piece it checks that only the intended bytes changed.

- In the boot package it changes the selector instruction, the U-Boot and
  package checksums, and 24 device-tree bytes: the `debug_mode` value and the
  five status values. On targets with a `panel_timing` profile it also changes
  `lcd_ht` and `lcd_vt` (see [60 Hz panel timing](#60-hz-panel-timing)).
- In the boot image it changes the header sizes, the command line and the
  image ID, and it replaces the kernel and the ramdisk. It keeps the load
  addresses and the appended device tree.
- In boot0 it changes the debug byte and the checksum of the copy at 8 KiB.
  boot0 keeps a second copy at 256 KiB and rewrites the first sector of both
  copies on every boot, so the second copy takes the new setting at the first
  start. The RG SP showed this.

The tool also derives the gzip-only pair that release 1.3.0 shipped. The
migration needs those hashes to recognise cards that carry that pair.

`build-image.sh` installs and verifies all three pieces in fresh images.
`build-rootfs.sh` puts the boot package, the boot image and a manifest in
`/usr/share/baseos/boot-gzip/`. An ordinary `baseos-update/1` archive can then
deliver the migration to existing cards. These files add about 8 MiB to the
rootfs.

### 60 Hz panel timing

The vendor panel timings refresh below 60 Hz. U-Boot and the kernel both take
the timing from the package's device tree, at boot and again on every wake.
For targets whose 24 MHz pixel clock is exact, `manifest/kernel-gzip.json`
gives new totals that shorten only the front porches. The porches, sync widths
and clock stay as the vendor set them, and the tool refuses a total that would
leave no front porch.

| Target | Stock HT×VT | Stock rate | 60 Hz HT×VT | Rate |
| --- | --- | --- | --- | --- |
| RG28XX | 606×686 | 57.732 Hz | 584×685 | 59.994 Hz |
| RG35XX H, RG35XX Pro | 770×528 | 59.032 Hz | 759×527 | 60.001 Hz |
| RG35XX Plus | 770×526 | 59.256 Hz | 759×527 | 60.001 Hz |
| RG35XX SP | 720×560 | 59.524 Hz | 713×561 | 60.001 Hz |
| RG40XX H, RG40XX V | 770×522 | 59.710 Hz | 759×527 | 60.001 Hz |

NextUI's measured rates for the RG28XX (57.7317 Hz) and RG40XX V (59.7104 Hz)
match the stock arithmetic exactly. The RG34XX, RG34XX SP and RG SP (26 MHz)
measure 59.569 Hz against 59.155 Hz from their device tree, so their real
pixel clock is not the nominal one. They, and the RG CubeXX (41 MHz), keep the
vendor timing until a measurement gives their clock.

The native loader programs the display itself from `display-engine.json` and
does not read this device tree before Linux. Native releases still need their
TCON values and package pins updated to match.

## Migration of existing cards

Copy the target's normal `.bosupd` onto the frontend card and restart. The
updater installs the rootfs and restarts. The new system shows an
optimising-startup message on its first boot, replaces the boot files,
confirms the update and starts the frontend. The kernel is identical in every
boot state, so the device does not restart again. The next ordinary boot uses
the new files.

`baseos-boot-migrate` identifies the installed boot package and boot image by
hash. It knows four states and refuses any other.

| Installed | Meaning | What the migration does |
| --- | --- | --- |
| original | The vendor files, with an uncompressed kernel. | It writes both files and then verifies them. The card cannot boot between the two writes. |
| gzip | The pair that release 1.3.0 installed. | It writes and verifies the boot image, then writes and verifies the boot package. The card can boot after every step. |
| fast_v1 | The fast pair before 60 Hz panel timing. Same image, older package. | It writes and verifies the boot package only. |
| fast | The current files. | It writes nothing. |

The migration hashes exactly the bytes that the image header describes.
It ignores and preserves whatever follows the image in the partition. An
interrupted earlier run can leave a gzip file beside a fast file. The
migration recognises that mixture and completes it.

The migration changes boot0 last. boot0 rewrites its own first sector on the
card at every boot with a new checksum and memory-training values, so a hash
can pin only the bytes after that sector. The migration checks the magic, the
length, the checksum and the pinned bytes. It then writes that one sector back
with the debug byte cleared and the checksum adjusted, and reads all of boot0
again to verify it. If boot0 fails any check, the migration leaves it
untouched, still replaces the other two files, and records the reason.

### Checks, failure handling and recovery

Before it writes, the migration checks the target, the partition geometry, the
hashes of the replacement files, and that userdata is persistent and writable.
It also needs external power or at least 50% battery. Without either it leaves
no marker and tries again at a later boot. Charging-only and USB-storage
sessions do not migrate.

The migration makes sure a recovery copy is on userdata before the first
write. If the first migration's copy of the original pair in
`/data/boot-gzip-v1/` is intact, that copy is enough. Otherwise the migration
saves the installed boot package and the installed image's own bytes in
`/data/boot-pair-v2/`. It never modifies `/data/boot-gzip-v1/`.

If a write or a readback fails, the migration writes the previous files back
and verifies them. If that verification also fails, it stops frontend startup
and update confirmation, asks on screen to keep power on, and leaves ADB
running. Do not restart the device in that state. The previous files are in
`/tmp`.

**A power loss during the migration can leave the card unbootable.** The A/B
rootfs slots do not cover the boot areas. The boot package has no second copy.
boot0 has one, but nobody has tested whether the boot ROM falls back to it.
Coming from the gzip pair, the card still boots if power fails between two
steps. Power lost during a write can leave that file half written. Coming from
the original pair, power lost at any point after the first write leaves the
card unbootable. To repair such a card, write the saved files back from
another computer or reflash it.

`/data/boot-pair-v2/migration.log` records each run. Every finished run also
leaves `complete-60hz`, which is the marker startup tests, so a card that
finished before 60 Hz panel timing runs the migration once more. `complete` holds
`installed`, `already-installed`, `rolled-back` or `skipped:` with a reason,
and a note if the migration left boot0 unchanged. Later boots test only that
`complete` exists, with a shell builtin, and start no helper.

Only a manual slot flip can start an older rootfs on the new boot files. That
rootfs still boots, with two faults. It lacks the wait for the second card slot
described in [rootfs and init](01-rootfs-and-init.md), so it may pick TF1 as
the frontend card. It also lacks `h700_late_usb.ko`, so five USB host
controllers stay off.

## Validation and timing

`tests/test-fast-boot-emulation.py` and `tests/test-kernel-gzip-emulation.py`
run each target's own U-Boot under Unicorn against the exact boot image the
build ships. For all eleven targets U-Boot asks the card for exactly the
image's bytes, unpacks the original kernel, copies the 512-byte ramdisk and
appends the header command line to `bootargs`. The tests model the hardware
calls. They run U-Boot's functions, not a whole boot, and they say nothing
about how each target's kernel responds. Every target's kernel does contain
the three arguments and the ext4 options and error counter that `rcS` uses.
boot0's code is byte-identical on all eleven targets. Only its first 0x1300
bytes differ, and they hold per-board settings such as memory timings.

`tests/test-kernel-gzip.py` checks the derived files and the corruption gates
for every target. `tests/test-boot-migration.sh` runs the real migration script
on ordinary files in a disposable Alpine container. It covers every
transition, a failure injected at each write, the rollback, the boot0 checksum
arithmetic, and one run with the files the build produces for the RG SP.
`tests/test-root-mount.sh` covers the root-partition steps in `rcS`.
`tests/test-late-usb.py` checks, for every target, that the module knows every
controller the build holds back. `h700_late_usb.ko` builds for all eleven
kernels, and every symbol version it imports matches each of them.

An RG SP, an RG40XX V and an RG34XX SP have booted these files. On the RG SP, a card running
release 1.3.0 took the update from a `.bosupd` and migrated on its first boot.
The updater later installed the final build of this branch the same way, and
that build passed all 39 checks of `validate-on-device.sh`. Warm
restarts with USB attached gave these medians, in seconds from the hardware
counter's origin:

| | Before Linux | Linux to handoff | Combined |
| --- | ---: | ---: | ---: |
| Release 1.3.0, 3 boots | 3.46 | 2.26 | 5.73 |
| Everything except the USB change, 4 boots | 2.72 | 1.70 | 4.41 |

The USB change was measured later with TF2 removed, so NextUI started from
TF1. Medians of three boots each:

| | Before Linux | Linux to handoff | Combined |
| --- | ---: | ---: | ---: |
| Everything except the USB change | 2.76 | 1.63 | 4.37 |
| With the five controllers started by the module | 2.77 | 1.34 | 4.17 |

Measured one change at a time, serial output in the three stages costs about
0.7 s, the initramfs 0.5 s, the USB host controllers 0.3 s, the slow RCU grace
periods 0.2 s and the two waits removed from `rcS` under 0.1 s. Those runs were separate, so the parts do not
sum exactly to the difference. About one boot in twelve spent 0.3 to 0.6 s
longer before Linux. Three of those four boots followed a large write to the
card.

On the new files the RG SP slept for 30 seconds, woke on an RTC alarm and
resumed with the frontend, Wi-Fi, GPU and all six USB host controllers
working. A cold power-on with TF2 removed took 4.38 s before the USB change
and used TF1 without waiting for the empty slot. A USB audio adapter on the
OTG port worked as a host device after the USB change. The OTG port uses host
controllers of its own, which the kernel starts on demand, so that test does
not cover the five controllers the module starts. The RG SP has no known
connector for those.

An RG40XX V with release 1.3.0 and the Slot frontend on TF2 took the same kind
of update. It migrated on its first boot and started on the new files at the
next restart, with all five held-back controllers running and TF2 chosen as
the frontend card. Its time to the handoff fell from 5.93 s to 4.22 s. It also
passed a 25-second sleep. An RG34XX SP with Slot on TF2 did the same, and its
time to the handoff fell from 7.44 s to 5.79 s. The other eight models have not
booted these files at all.
[The experiment notes](../experiments/h700-boot-speed/README.md) list each
measurement, and [boot profiling](09-boot-profiling.md) explains how the stages
are measured.
