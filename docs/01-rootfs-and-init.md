# 01 — Root filesystem and init

The active rootfs is a 512 MiB ext4 slot containing BusyBox, harvested vendor
libraries and BaseOS tools. `/bin`, `/sbin` and `/lib` link into `/usr`. In a
composed image `/usr/lib` is a squashfs file that `rcS` mounts over the
directory. See [the `/usr/lib` image](#the-usrlib-image).

## 1. Build inputs

`build-rootfs.sh` combines:

- Static BusyBox and tools built by `build-tools.sh`: Dropbear, SFTP server,
  curl with CA certificates, adb, Avahi, `fbsplash`, `gptgrow`, `gptslot`,
  `axp-off` and `charger-wait`.
- Vendor userspace selected by `manifest/harvest.list`: glibc, Mali/EGL/GLES,
  ALSA, wireless services, Bluetooth/BlueALSA/D-Bus, timezone data and modules.
- ARMHF compatibility libraries and loader, including stock fbdev Mali and
  SDL2 under `/usr/lib32`. These are required independently of radio support.
- `overlay/`, copied last, plus generated device identity and runtime symlinks.

Preparation dereferences harvest paths inside the extracted vendor root and
normalizes the archive. List sonames in the allowlist. Changing required paths
requires preparing the affected targets again and refreshing the cache;
`tools/verify_harvest.py` rejects incomplete archives during build, pack and restore.

The rootfs build checks the AArch64 dependency closure and the ARMHF loader/libc
required by `rtk_hciattach`. Other ARMHF applications still need their own
compatibility validation. Shipping `libudev` does not provide a udev daemon.
GPU module debug data is stripped with ABI checks; ARMHF libraries are retained
as harvested. See [boot performance](09-boot-profiling.md).

## 2. Runtime layout

| Path | Purpose |
| --- | --- |
| `/data` | Persistent p6 state: SSH keys, Bluetooth, entropy, time preferences and update state |
| `/usr/lib` | Read-only squashfs image of the libraries, kernel modules and firmware |
| `/mnt/sdcard` | Active frontend card: usable TF2 p1, otherwise TF1 p7 |
| `/mnt/SDCARD` | Compatibility symlink to `/mnt/sdcard` |
| `/mnt/system` | Temporary read-only TF1 mount for configuration and update discovery when needed |
| `/run`, `/tmp`, `/var`, `/dev/shm` | tmpfs runtime state |
| `/mnt/vendor` | Compatibility files shipped in the rootfs; no vendor partition mounted here |
| `/usr/lib/baseos-gnu/bin` | GNU tools from stock for PortMaster ports. First on the frontend's PATH; BaseOS scripts keep BusyBox in `/usr/bin` |

The root filesystem is mounted read-write. On a card that still boots through
the vendor initramfs, the initramfs mounts it that way. Otherwise the kernel
mounts it read-only and `rcS` remounts it writable with the same options. See
[boot files](12-kernel-gzip.md). Normal initialization does not remount it
read-only. If mounting p6 fails, `rcS` uses tmpfs for `/data`, so
state from that boot is not persistent. Shutdown flushes persistent mounts;
see [power handling](05-runtime-power-network.md).

### The `/usr/lib` image

`build-image.sh` packs `/usr/lib` into the squashfs file
`/usr/share/baseos/usr-lib.sqfs` and leaves the directory empty. `rcS` mounts
the file read-only over `/usr/lib` through a loop device, right after it has
dealt with the root filesystem. Everything `rcS` runs before that is static:
BusyBox and the bundled `e2fsck`. A bare rootfs tree, such as the one the QEMU
test boots, has no image file and keeps its libraries in the directory.

The image exists because the card is slow. It reads about 20 MB/s, and a
frontend loads most of the 19 MB GPU library before it can draw. The image
uses LZ4 HC with 128 KiB blocks. That halves the bytes, and the processor
unpacks LZ4 faster than the card delivers. On the RG SP NextUI drew its first
frame about 0.7 s earlier, and the time to the handoff did not change. On an
RG40XX V, Slot read 18 MiB from the root partition before its first frame
where release 1.3.0 read 32 MiB. gzip
and xz images are smaller, but unpacking them takes longer than the read they
save. [The experiment notes](../experiments/h700-boot-speed/README.md) have
the measurements.

The image has these consequences.

- `/usr/lib` is read-only, including the kernel modules and firmware under
  it. To try a changed library or module on a device, load it from another
  path or build a new image.
- The root stays ext4 and writable, and an update payload is still one slot
  image. A card on an older release takes the update as before.
- The kernel caches the compressed blocks as well as the unpacked pages. Both
  are page cache, which the kernel gives up when a program needs the memory.
- If the image does not mount, no dynamic program can start. `rcS` writes a
  line to the kernel log and carries on with its static tools.
  `frontend-session` does not confirm an update on such a boot, so the third
  failed boot flips back to the previous slot.

The build guards against that case twice. `tools/lib_image.py` reads each
target's kernel configuration and refuses a kernel without the loop driver or
LZ4 squashfs built in. `build-image.sh` unpacks the image again and compares
every name, mode, owner, link and content with the directory before it
empties the directory.

## 3. Init and startup order

The kernel, or the vendor initramfs on a card that still has one, runs the
regular executable `/init` script, which executes BusyBox init. `/etc/inittab` runs `rcS`, then respawns
`frontend-session` and a serial getty. It invokes `rcK` on shutdown.

`rcS` performs this sequence:

1. Mount kernel interfaces. Give `/dev/input` the modification time 1, which
   SDL's gamepad scan without udev needs; see
   [boot files](12-kernel-gzip.md#a-side-effect-on-devinput). If the kernel mounted the root filesystem
   read-only, run `e2fsck` when the filesystem has recorded errors, then
   remount it writable. Mount the `/usr/lib` image. Mount the tmpfs filesystems. Apply SP Super Standby
   where supported. Mount `debugfs` for the HDMI display-control and MENU GPIO
   interfaces.
2. Handle charger-origin startup before ordinary initialization; see
   [charger-only boot](11-charger-only-boot.md).
3. Start Mali in the background. Load `h700_late_usb.ko` in the background as
   well, which starts the USB host controllers that the
   [boot files](12-kernel-gzip.md) hold back. Mount `/data`, and check the
   system-update trial.
4. Restore time preferences and entropy, and read the RTC as UTC with
   `hwclock -u -s`. Start radio initialization in the background. It loads the
   Bluetooth wake-handshake module first and the Wi-Fi module after it, with a
   remembered power-off retry for radios that reset slowly. See
   [runtime notes](05-runtime-power-network.md).
5. Restore or generate the machine ID, then run first-boot storage expansion.
6. Wait for a TF2 card that the kernel is still identifying, at most until
   the handoff budget ends. An empty slot costs one file read. Sample MENU
   through the GPIO check in `boot-menu-held`, or use the marker that charger
   handling kept. Select USB-storage mode before mounting frontend storage.
7. For normal startup, mount usable TF2 p1 or fall back to TF1 p7. Apply
   `baseos.conf` from TF1, then scan for system updates. When TF1 was mounted
   only for its settings, unmount it in the background, because the unmount
   takes about 60 ms. Storage mode uses configuration defaults and skips card
   access and update application.
8. Start SSH/SFTP and USB adb through `/etc/init.d/dev` in the background.

Services requiring a network do not block frontend startup. There is no udev
or mdev; devtmpfs provides device nodes. Bluetooth starts D-Bus on demand.

## 4. Frontend session

`frontend-session` confirms an update trial when the session starts, even if no
frontend is installed. In USB-storage mode it waits for the gadget result,
displays the storage status and keeps the frontend stopped.

Normal sessions retry card mounting, run pending `MinUI.zip`/`*.pakz` installers,
then select these entry points in order:

1. Generic frontend: `System/frontend` through `/lib/ld-linux-aarch64.so.1`, or
   `System/launch_frontend.sh` through `/bin/sh` if the binary is absent.
2. `System/slot`, invoked through `/lib/ld-linux-aarch64.so.1`.
3. `.system/h700/paks/MinUI.pak/launch.sh`, invoked through `/bin/sh`.
4. `spruce/scripts/runtime.sh`, invoked through `/bin/sh` using the
   `/mnt/SDCARD` alias for the selected card.

Only regular files on the mounted card qualify. Spruce's runtime owns its
startup and uses its own helpers from the extracted release; BaseOS does not
unpack spruceOS archives. Shell entry points do not need an executable bit.

The session exports every variable from `/etc/baseos-release` to installers and
all frontend entry points, including `BASEOS`, version, build, and device metadata.

Slot uses `SLOT_ROOT=/mnt/sdcard` and that working directory. Its release is
extracted on a computer; BaseOS does not unpack Slot archives. Invoking the
loader supports copied binaries without an executable bit. Its optional
AGS-102 `ags-net` helper is not shipped by BaseOS.

Generic frontends run with `/mnt/sdcard` as their working directory, without
`SLOT_ROOT`. The binary must target BaseOS's glibc AArch64 runtime, like Slot;
the loader supports binaries without an executable bit. Use the shell launcher
to configure a different runtime or additional environment variables. Generic
stdout/stderr go to `/tmp/generic.log`, replaced on each launch. If the selected
launcher fails, its exit status goes to init for respawn; it does not fall
through to another entry point.

Before the first handoff, the session performs a bounded wait for `/dev/mali0`
and records `/run/boot-frontend-exec`. The marker survives session respawns.
Missing cards, missing frontends and installer errors display a status pill;
see [boot splash](04-boot-splash.md).

## 5. Configuration and compatibility

`baseos-config` reads TF1's `baseos.conf` before network services start and
writes `/run/hostname`, `/run/hosts` and `/run/baseos.conf`. `/etc/hostname`
and `/etc/hosts` link to those generated files. See
[network identity](05-runtime-power-network.md#hostname-and-mdns).

- `systemctl` handles Bluetooth start, restart, stop and `is-active`.
  Unsupported `is-active` queries return 3; other unsupported operations succeed
  quietly. Starting Bluetooth ensures D-Bus is running first.
- `timedatectl` supports `show` for Timezone/NTP/NTPSynchronized,
  `set-timezone`, `set-ntp` and the boot-time `apply` operation. Preferences live
  in `/data/timezone` and `/data/ntp-enabled`; the runtime timezone link is
  `/run/localtime`. NTP defaults off and runs asynchronously when enabled.
- `/mnt/vendor/ctrl/setBluetooth.sh` supplies the vendor-compatible Bluetooth
  initialization interface.
- `poweroff` and `reboot` route through BaseOS shutdown wrappers; see
  [power handling](05-runtime-power-network.md).

`/etc/baseos-release` includes exact target, frontend family, model string,
panel rotation, version and build. The `dmenu.bin` model-string stub supports
frontends that detect hardware through the vendor path. Device profiles are
maintained in `devices.json`.

## 6. Logs

BaseOS boot, session, installer, update and adb diagnostics append to
`baseos-boot.log` at the root of the active frontend card. Early messages use
`/data/baseos-boot.log` until the card mounts; missing/unwritable cards and
USB-storage mode use the fallbacks described in [boot I/O](10-boot-io-audit.md).
Slot output remains in `/tmp/slot.log`, and NTP output in `/var/log/baseos-ntp.log`.
