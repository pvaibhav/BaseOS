# 05 — Runtime: boot timing, power/sleep, network

All numbers here were measured on the RG40XXV running base OS (2026-07-19), not
estimated.

## 1. Boot timing (measured)

Kernel-relative markers are written to `/run/boot-*` by `rcS` / `nextui-session`
(seconds since kernel start):

| marker | warm boot | meaning |
|---|---|---|
| `boot-rcS-start` | 2.04 s | our init reached the first breadcrumb (proc/sys/dev/tmpfs mounted) |
| `boot-rcS-done` | 2.59 s | modules requested, `/data` + card mounted (~0.55 s of rcS) |
| `boot-frontend-exec` | 2.80 s | `launch.sh` handed control to NextUI |
| `boot-dev-done` | TBD | `/etc/init.d/dev` finished starting dropbear + launching the adb gadget script |
| `boot-adb-gadget-done` | TBD | the adb gadget bound its UDC (off the critical path — see §6) |

> The `dev` / `adb-gadget` markers are **measured per release**: they sit off the
> critical path (§6) and are re-measured, not carried forward, whenever that area
> changes. `validate-on-device.sh` prints them on every run.

Then `launch.sh` + `nextui.elf` init add ~1–2 s to the first frame. Before the kernel,
boot0 + U-Boot add ~1.5–2.5 s (not software-visible; `bootdelay=0`).

**Stopwatch cold boot: 7.18 s** power-on → NextUI (vs ~15–20 s on stock). The stock
kernel eats the fixed first ~2 s and is untouchable here (rebuilding it is the
separate NextOS project). The `mali_kbase` background-load optimisation (below) shaved
frontend-exec from 3.04 s → 2.80 s.

### `mali_kbase` background load

`mali_kbase.ko` costs ~0.73 s to insmod, but nothing needs the GPU until NextUI's
`GFX_init` ~2 s later. `rcS` loads it in the **background** so it overlaps the card
mount and `launch.sh` startup; `nextui-session` waits (bounded) for `/dev/mali0`
before the hand-off. Measured: the wait was 0.1 s — almost the entire GPU load is now
hidden. Idle system: **74 MB RAM used / 973 MB**, rootfs 105 MB, CPU 44–45 °C at
`schedutil`.

## 2. Deep sleep — validated real, on hardware

The headline goal. Confirmed genuine **suspend-to-RAM**, not fake/freeze sleep, from
the kernel log after a real sleep/wake cycle:

```
PM: suspend entry ... 14:22:28
PM: Suspending system (mem)
PM: suspend of devices complete after 923.702 msecs
Disabling non-boot CPUs ...
Enabling non-boot CPUs ...
PM: suspend exit ...  14:58:01
```

`PM: Suspending system (mem)` = real STR (the thing mainline H616 / ROCKNIX can't do —
they ship fake suspend). `Disabling non-boot CPUs` = the other 3 cores actually
powered down. Over the **35.5-minute** sleep the AXP2202 coulomb counter
(`charge_counter`) **did not move** (1,952,000 µAh → 1,952,000 µAh, 61 % → 61 %) — a
flat counter that fake sleep at tens of mA would have visibly drained. This runs on an
OS we fully control, with no stock trampoline.

The suspend mechanism is unchanged from the port: `.system/h700/bin/suspend` echoes
`mem` to `/sys/power/state` directly (no systemd involvement); AXP2202 power-button
wake, lid handling and the WiFi bounce all stay as shipped. `alsactl` (harvested)
saves/restores the mixer across sleep.

**Measuring exact standby µA** needs a longer sleep — the coulomb counter is coarse
(no tick over 35 min at this draw; `current_now` reads empty). `diagnostics/sleep-drain/`
provides `pre-sleep.d`/`post-resume.d` hooks that stamp `charge_counter` + RTC time at
the exact suspend/resume boundary and log the delta to `/mnt/sdcard/sleep-drain.log`;
leave the device asleep for hours to get a projected-standby figure. (Hook files must
end in `.sh` — NextUI's `run_hooks.sh` only executes `*.sh`.)

## 3. WiFi — Base OS owns the interface

The RTL8821CS module (`8821cs.ko`) triggers an **asynchronous** SDIO probe that creates
`wlan0` ~2 s after the insmod returns. If a frontend's boot-time wifi bring-up runs
before `wlan0` exists, its `ip link set wlan0 up` / `wpa_supplicant -i wlan0` silently
fail and WiFi never comes up until the user toggles it.

To keep this **independent of any frontend**, Base OS owns the `wlan0` interface itself:
`rcS` runs a background task that loads the module, **waits for `wlan0` to appear
(bounded), then `rfkill unblock` + `ip link set wlan0 up`** (`ip`/`rfkill` are BusyBox
applets). By the time a frontend wants WiFi, the interface is present and up, so the
frontend only has to run `wpa_supplicant`/DHCP on it — no race-hardening needed in the
frontend's own scripts.

> Note: `wlan0` appears at ~5 s on the current timing (module init is the slow part).
> A frontend that starts `wpa_supplicant` *very* early could still beat it; the robust
> guarantee is that Base OS brings the interface up as soon as hardware allows and does
> not depend on the frontend to wait. NextUI additionally waits for `wlan0` in its own
> `wifi_init.sh` (belt-and-suspenders), but Base OS no longer relies on that.

- **Power-save.** `rtw_power_mgnt=2` (driver default) — **identical to stock**, kept as
  correct for a handheld's battery. It causes intermittent ICMP latency (aggressive
- **Power-save.** `rtw_power_mgnt=2` (driver default) — **identical to stock**, kept as
  correct for a handheld's battery. It causes intermittent ICMP latency (aggressive
  pings from a host monitor flap), but the association is rock-solid; not a fault.
- DHCP: base OS uses BusyBox `udhcpc` (Ubuntu had `dhclient`); `wifi_init.sh` already
  prefers `dhclient` and falls back to `udhcpc`, and the `udhcpc` event script writes
  `/run/resolv.conf` (rootfs is otherwise rw but `/etc/resolv.conf` is a baked symlink
  into `/run`).

## 4. Bluetooth audio

Uses the harvested stock stack directly: `rtk_hciattach` attaches the RTL8821CS UART,
`bluetoothd` (BlueZ 5.66) runs under a base-OS `dbus-daemon` started by the
`systemctl` shim, and `bluealsa` provides the A2DP source. NextUI's `bt_init.sh` drives
it unchanged via the `setBluetooth.sh` shim ([01](01-rootfs-and-init.md) §7). Validated
as far as daemons-run on-device (chroot); full pairing/audio is on the hardware
to-do in [06](06-status-and-lessons.md).

## 5. Power button / poweroff

There is no `systemd-logind` on base OS. NextUI's `keymon` reads the power key
(event0, `axp2202-pek`) directly and writes `/tmp/poweroff` / `/tmp/reboot` sentinels
that `launch.sh` acts on; BusyBox init runs the poweroff/reboot. A **long-press powers
off in hardware** at the AXP2202 PMIC regardless of software — normal, expected
force-off behaviour.

## 6. USB gadget — adb over the charge port

The single USB-C port is a charge port that also exposes a USB peripheral controller.
Base OS drives it as a Google adb gadget so a host can `adb shell`/`adb push`/`adb pull`
over the cable — **USB only, no TCP** by design (same root/no-auth dev posture as the
root/root dropbear; keeping it off the network avoids exposing an unauthenticated shell
over WiFi). `/usr/sbin/usb-gadget-adb` composes the gadget; it is launched **backgrounded
from `/etc/init.d/dev`** (itself already backgrounded off `rcS`, [01](01-rootfs-and-init.md) §5).

**Role: leave the sunxi manager alone.** The port defaults to the sunxi manager's auto
(ID-pin/VBUS) detection, and that is exactly what we rely on: the built-in UDC
(`5100000.udc-controller`) is always present, we bind our gadget to it, and the manager
selects peripheral mode on its own when a host cable is attached. The script does **not**
force the role, because every way of doing so is a trap on this vendor kernel
(measured on rg40xxv, [06](06-status-and-lessons.md) §3):

- Writing `/sys/.../usbc0/otg_role` (e.g. `echo usb_device > otg_role`) **wedges the
  writer in an uninterruptible D-state** — reproduced both with and without a gadget
  bound — so a boot script that wrote it would leak a stuck process and never bring adb
  up.
- The sibling files `usb_device`/`usb_host`/`usb_null` are 0400 **read-triggers** — a
  bare `cat` of one switches the role and can wedge the port.

Leaving the role in auto mode also keeps charging on the shared USB-C port undisturbed.
(Note the real device path is `/sys/devices/platform/soc/usbc0`, reached via the stable
`/sys/bus/platform/devices/usbc0` symlink — but the script needs neither.)

**configfs gadget layout.** The stock 4.9.170 kernel has `CONFIG_USB_CONFIGFS_F_FS=y`
built in, so no module is needed. The script builds, under
`/sys/kernel/config/usb_gadget/g1`:

- IDs `idVendor 0x18d1` / `idProduct 0x4e42` (Google / adb);
- `strings/0x409/serialnumber` from `androidboot.serialno` on the kernel cmdline
  (falls back to a constant if absent), plus manufacturer/product strings;
- one function `functions/ffs.adb`, a **FunctionFS** instance, linked into
  `configs/c.1`;
- the FunctionFS mount at `/dev/usb-ffs/adb`, where `adbd` opens `ep0` and writes its
  descriptors.

**Start + bind ordering.** The UDC bind is the last step and it only succeeds **after**
`adbd` has written its descriptors to `ep0`. So the script starts `adbd` (as root)
first, then **retries** writing the controller name into `g1/UDC`
(`/sys/class/udc/5100000.udc-controller`) until the bind takes. Binding before `adbd`
has populated ep0 fails with `EINVAL`; the retry loop closes that race without a fixed
sleep.

**Idempotency / suspend-resume.** The script is safe to re-run: if `g1` already exists
and is bound it is a no-op. The gadget survives deep-sleep (§2) — the controller
re-enumerates on resume with the descriptors still in place; nothing re-composes it.

**Boot-time stance.** Nothing here is on the critical path. The gadget script is
backgrounded off the already-backgrounded `init.d/dev`, so it never delays
`frontend-exec`. Two measurement hooks make the cost
observable: the script writes `/run/boot-adb-gadget-done` when the UDC bind lands and
appends `adb gadget ready` to `/mnt/sdcard/baseos-boot.log`; `init.d/dev` writes
`/run/boot-dev-done` when it finishes. Because these live off the critical path, the
boot-timing table (§1) leaves them **TBD / measured per release** — re-measure and
record them whenever this area changes rather than trusting a stale number.
