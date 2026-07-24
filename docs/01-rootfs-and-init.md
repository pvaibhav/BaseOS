# 01 — Root filesystem & init

The rootfs on p5 is what we build. It is a minimal BusyBox userland plus a small,
exactly-scoped set of stock libraries and daemons. ~105 MB in a 512 MiB ext4.

## 1. Where the contents come from

Four sources, assembled by `build-rootfs.sh` (see [02](02-image-build-and-flash.md)):

1. **Static BusyBox** (Alpine `busybox-static`, ~1 MB) — provides `/sbin/init`, `sh`
   (ash), `mount`, `insmod`, `udhcpc`, `hwclock`, `getty`, `poweroff`/`reboot`,
   **and — importantly — `mkfs.vfat`/`mkdosfs`, `partprobe`, `blockdev`, `killall`**
   (used by the first-boot expand, [03](03-first-boot-and-expand.md)).
2. **The StockMod harvest** — an allowlist (`manifest/harvest.list`) extracted from
   the selected target's p5 by `prepare-stock.sh`. The allowlist was established from
   the hardware-tested runtime closure and contains the libraries and daemons we keep:
   - glibc 2.35 + `ld-linux-aarch64` + `libnss_{files,dns}`
   - Mali blob `libmali.so.0.20.0` + `libEGL`/`libGLESv2`/`libGLESv1_CM` shims
   - ALSA: `libasound` + `/usr/share/alsa` + `/etc/asound.conf` + the alsa-lib plugin
     dir (incl. the bluealsa PCM/CTL plugins) + `alsactl` (the suspend script saves
     mixer state through it)
   - WiFi: `wpa_supplicant`, `wpa_cli` (+ libnl3), `fsck.fat`
   - Bluetooth audio: `bluetoothd` (BlueZ 5.66, at `/usr/libexec/bluetooth/`),
     `bluealsa`, `bluealsa-aplay`, `bluetoothctl`, `rtk_hciattach`, its ARMHF
     interpreter/libc, `hciconfig`,
     `libbluetooth`, `libsbc`, `dbus-daemon` + `/usr/share/dbus-1` + `/etc/dbus-1` +
     `/etc/bluetooth`, and `/lib/firmware/rtlbt/`
   - the transitive `ldd` closure of all of the above — ~45 libraries total
     (glib/gio for BlueZ, `libsystemd` *as a library* for dbus, expat, ffi, pcre2,
     blkid/mount, crypto/ssl for wpa, freetype/png16, stdc++, …)
   - the 3 kernel modules; `/usr/share/zoneinfo`; terminfo for `linux`/`vt100`/`xterm`
   - `ldconfig` (+ `ld.so.conf*`) — the build runs it to generate `ld.so.cache` so the
     multiarch dir resolves
3. **Static tools built once in a container** — Dropbear for dev SSH/scp, `curl`
   with a CA bundle for frontend HTTP clients, plus **`fbsplash`** and **`gptgrow`**
   (see [04](04-boot-splash.md), [03](03-first-boot-and-expand.md)).
4. **Our overlay** (`overlay/`) — copied last, wins over everything. It includes
   the small service/time compatibility shims needed by NextUI.

**Harvest gotchas learned on-device** (all fixed):
- Preparation extracts p5 with `debugfs`, then runs static BusyBox tar chrooted inside
  that root with `-h`, so soname paths become real files and absolute symlinks cannot
  escape into the container. List sonames in `harvest.list`, not fully-versioned names.
- The interp compat symlink `/lib/ld-linux-aarch64.so.1` must exist or **every**
  dynamic binary is dead. `bluetoothctl` additionally needs `libreadline`/`libtinfo`.
  The stock `rtk_hciattach` is the one ARM32 binary and needs the harvested ARMHF
  loader/libc pair. These were found by on-device validation; the build now checks
  the complete AArch64 closure and the ARMHF pair before emitting `rootfs.tar`.

## 2. Merged-`/usr` layout

The Ubuntu harvest assumes merged-`/usr`, so the rootfs uses it: `/bin → usr/bin`,
`/sbin → usr/sbin`, `/lib → usr/lib` are symlinks; real content lives under `/usr`.
Overlay files that live "in `/sbin`" (e.g. `nextui-session`) are therefore placed in
`overlay/usr/sbin/`.

## 3. `/init` — a staged-marker probe script

The kernel cmdline is `init=/init`. The vendor initramfs `switch_root`s into our
rootfs and execs `/init`. Our `/init` is a **regular script** (not a symlink — see
[00](00-boot-chain-and-partitions.md) §2) that:

1. writes a raw liveness marker into the sacrificial p6 stub sector (proves BusyBox
   `sh` ran even if every later mount fails — pure forensics, see [06](06-status-and-lessons.md));
2. paints the first `fbsplash` frame;
3. `exec /usr/bin/busybox init`.

BusyBox init then reads `/etc/inittab`.

## 4. `inittab`

```
::sysinit:<raw p6 liveness marker>
::sysinit:/etc/init.d/rcS
::respawn:/sbin/nextui-session
ttyS0::respawn:/sbin/getty -L ttyS0 115200 vt100   # serial console (harmless without a cable)
::ctrlaltdel:/sbin/reboot
::restart:/sbin/init
::shutdown:/etc/init.d/rcK
```

`nextui-session` is `respawn`ed: it replaces the entire stock chain
(`launcher.service → launcher.sh → loadapp.sh → dmenu_ln → boot shim`).

## 5. `rcS` — early init (target well under 1 s)

1. mount `proc`, `sysfs`, `devtmpfs`, `devpts`, tmpfs on `/dev/shm` `/tmp` `/run`
   `/var`; hostname
2. paint `fbsplash 30`
3. **`insmod mali_kbase.ko` in the background** — nothing needs the GPU until
   NextUI's `GFX_init` ~2 s later, and the insmod costs ~0.7 s; backgrounding it
   overlaps the card mount (see the timing win in [05](05-runtime-power-network.md))
4. mount p7 (`UDISK`) → `/data` (persistent state); create `/data/{bluetooth,bluealsa,
   dropbear}`; symlink `/var/lib/bluetooth → /data/bluetooth`; restore the persisted
   timezone through `/run/localtime`
5. restore the entropy seed; `hwclock -s` (background); `insmod 8821cs.ko` (background)
6. `machine-id`: reuse `/data/machine-id` or generate one; symlink `/etc/machine-id`
   and `/var/lib/dbus/machine-id → /run/machine-id`
7. **first-boot expand-to-fill** (`expand-storage`, [03](03-first-boot-and-expand.md))
   — runs *before* the card mount; a no-op once the card is provisioned
8. mount the NextUI card: TF2 (`/dev/mmcblk1p1`) if present, else this card's own
   `/dev/mmcblk0p8` → `/mnt/sdcard`, plus the `/mnt/SDCARD` compat symlink; write a
   boot breadcrumb to the card
9. paint `fbsplash 55`; start dev extras (`/etc/init.d/dev` → dropbear SSH, plus the
   backgrounded adb-over-USB gadget via `usb-gadget-adb`, see
   [05](05-runtime-power-network.md) §6) in the background

No udev, no mdev: devtmpfs auto-creates nodes, SDL runs with
`SDL_JOYSTICK_DISABLE_UDEV=1`, BlueZ makes its own uinput nodes, and `dbus-daemon`
starts on demand from the BT path — not at boot.

## 6. `nextui-session` — the frontend loop

Runs from `respawn`. It:

1. ensures the card is mounted (retry loop; `INSERT SD CARD` splash if none);
2. runs the first-boot **install** if `MinUI.zip`/`*.pakz` are present — same triggers
   as the old boot shim — painting a static `INSTALLING` frame
   (see [04](04-boot-splash.md) for why it is static, not animated, and why NextUI's
   own installer UI can't render here);
3. waits (bounded) for `/dev/mali0` (the backgrounded module load), records the
   `frontend-exec` boot marker, paints `fbsplash 100`, and
   `exec /bin/sh .system/h700/paks/MinUI.pak/launch.sh`.

The existing `MinUI.pak/launch.sh` runs **unchanged** on base OS: its stock-OS calls
(`systemctl …`, `killall brightCtrl.bin cexpert`, the logind drop-in, the TF1 dmenu
self-heal) are already guarded with `|| true` / `command -v` / `mountpoint -q`, and
resolve harmlessly against our shims. Poweroff/reboot work via the sentinels NextUI
already writes (`/tmp/poweroff`, `/tmp/reboot`) — BusyBox init handles both.

NextUI's RetroAchievements HTTP layer also runs unchanged: it invokes the static
`/usr/bin/curl` supplied by BaseOS. The binary is built from a pinned curl release and
does not depend on the StockMod or frontend library trees.

## 7. Service shims — running NextUI's stock-OS scripts unchanged

Base OS has no systemd and no vendor scripts, but NextUI's h700 scripts call into
them. Three shims bridge the gap:

- **`/usr/sbin/systemctl`** — a ~40-line POSIX shim covering exactly the calls NextUI
  makes: `stop NetworkManager/wpa_supplicant*` → succeed no-op; `is-active bluetooth`
  → `pidof bluetoothd`; `start/restart bluetooth` → ensure a system `dbus-daemon` then
  launch `/usr/libexec/bluetooth/bluetoothd`; `stop bluetooth` → kill it. Everything
  else exits 0 quietly (callers guard with `|| true`).
- **`/usr/bin/timedatectl`** — implements NextUI's timezone query/set calls without
  systemd. The selected name is stored in `/data/timezone`; `/etc/localtime` points
  through writable `/run/localtime`, which `rcS` restores on every boot. Invalid or
  path-traversing zone names are rejected against the harvested zoneinfo database.
- **`/mnt/vendor/ctrl/setBluetooth.sh`** — a POSIX rewrite of the vendor script at the
  same path (p6 is never mounted over `/mnt/vendor`), so `bt_init.sh` works unchanged:
  `init` → `insmod rtl_btlpm.ko` + `rtk_hciattach …`; `enable` → `hciconfig hci0 up`.

Device identity is generated from `devices.json` at build time. `/etc/baseos-release`
separates the exact `BASEOS_TARGET`, frontend-family `BASEOS_DEVICE`, human model and
stock-style `BASEOS_MODEL_STRING`. A stub `/mnt/vendor/bin/dmenu.bin` containing that
model string keeps NextUI's `strings … | grep ^RG` detection working without the stock
frontend present; it is a compatibility adapter, not a BaseOS dependency on NextUI.

## 8. Read-only vs read-write root

The design target is a read-only root (writable state on tmpfs + `/data` + the FAT
card) for power-loss resilience. In practice the **vendor initramfs mounts p5
read-write** and we do not yet remount it `ro` — so today the root is rw. The journal
(required anyway, [00](00-boot-chain-and-partitions.md) §2) protects integrity;
remounting `ro` at the end of `rcS` is tracked as polish in
[06](06-status-and-lessons.md).
