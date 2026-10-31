# BaseOS 2 beta plan

Status 2026-11-01. Owner: Prashant. This plan is written for implementing agents:
each work package (WP) is self-contained. Read "Ground rules" first, then only the
WP you are given.

## Goal and definition of done

BaseOS 2 beta = **feature parity with BaseOS 1.4.0** (README of tag `v1.4.0`) on
all eleven H700 models, **no performance regression** against 1.4.0 on the same
unit, and a **transitional dual-boot**: BaseOS 1.x and 2.x live on the same card
and the user latches which one starts.

The beta ships when:

1. Every row of the parity matrix below is "done" on the RG28XX and RG SP
   (owner's units) and at least "built, boot-tested" on every other model by a
   tester (WP-30).
2. Every performance criterion (section "No regression") passes on the RG28XX
   and RG SP against 1.4.0 measured on the same unit.
3. A user can install, update, switch and uninstall BaseOS 2 without a computer
   (WP-01 to WP-04), following a short wiki guide (WP-40).

## Ground rules for every agent

- Repo: `/Users/pvaibhav/.t3/worktrees/BaseOS/feature-h700-mainline`, BaseOS 2 in
  `v2/`. Read `v2/CONTRACT.md` (frontend contract) and the notes in `v2/notes/`
  that your WP names. BaseOS 1.x is the rest of the repo; tag `v1.4.0` is the
  parity reference (`git show v1.4.0:README.md`, `v1.4.0:docs/`).
- Kernel: Linux 7.3.1 tree in docker volume `v2-kernel` via container `v2-kbuild`
  (`/k/linux`, build `/k/build`); build and export with `sh v2/kernel/build.sh`.
  Commit kernel changes in `/k/linux`. Only one agent edits `/k/linux` at a time;
  others use a git worktree in the volume (`/k/linux-<name>`, build `/k/build-<name>`).
- Root and payload: `python3 v2/os/build.py root|payload` (payload for the RG SP:
  `--firmware v2-rgsp --dtb work/v2/kernel/rgsp.dtb`; add `--dev` for a first try).
- Commits: author `Prashant Vaibhav <prashant@vaibhav.de>`, **no co-author or tool
  attribution lines**, plain everyday wording. Never push.
- Frontend repos: NextUI `/Users/pvaibhav/Code/Me/NextUI/.worktrees/baseos2-min`
  (branch `baseos2-min`), Slot `/Users/pvaibhav/Code/Others/slot/.worktrees/baseos2`
  (branch `baseos2`). Change a frontend only where BaseOS 2 needs it; every other
  speed-up belongs in BaseOS.
- Prefer Linux standard interfaces; add `baseos` commands only where no robust
  standard exists. No "fooling" shims (fake `/dev/disp`, LD_PRELOAD, SDL aliases).
- Device work (only WPs marked **device**): RG28XX adb `0c00105562458931c52`
  (test unit), RG SP adb `ac001089c89588720d2` (owner's daily unit: never touch
  games, saves, states, `Config/`, stock OS files). Tools: `v2/dev/lab.py`
  (`BASEOS_SERIAL=… BASEOS_FIRMWARE=v2-rgsp` for the RG SP). Anything that can
  cut off adb (kernel, device tree, USB, early start-up) goes out as a `--dev`
  payload first; its watchdog returns the unit to stock if adb disappears.
  adb kills processes started in a shell session: use `lab.py bg`. Never raw
  reboot; use `baseos power restart` or `lab.py`. Temporary files only in `/tmp`.
  No backups, rollback copies, manifests or installer leftovers on any card.
- Camera on the units: ffmpeg avfoundation `"Prashant’s iPhone mini Camera"`,
  `-pixel_format nv12 -video_size 1280x720 -framerate 30`; keep the Mac awake with
  `caffeinate -dims -t 7200` in the background while using it.
- Every WP ends with: what changed, how it was verified (real device result,
  build-only, or emulated, said plainly), and anything left open.

Agent tier hints: **S** = scripting/glue, a cheap agent is fine. **M** = moderate
systems work. **H** = firmware, kernel drivers or performance: use a strong agent.

## Parity matrix against BaseOS 1.4.0

| 1.4.0 feature | BaseOS 2 today | WP |
|---|---|---|
| Startup time (1.4.0 total ~4.1 s + frontend) | handoff ~1.7–1.8 s, menu ~3.7 s (RG28XX/RG SP) | WP-20 (keep) |
| Display, panel, brightness, logo | done RG28XX/RG SP; other 9 built, untested | WP-30 |
| Sound: speaker, headphones, volume | done; speaker/headphone pop fixes not ported | WP-11 |
| Controls, power key, volume keys | done RG28XX/RG SP | WP-30 |
| Analog sticks (40XX, Cube, H, Pro, 34XXSP) | separate joystick device, untested; check frontend expectations | WP-12 |
| Wi-Fi | done RG SP (scan, connect, DHCP, DNS) | — |
| Bluetooth (incl. audio) | controller + scan done; pairing/audio untested | WP-13 |
| HDMI (video + audio) | patch series, untested | WP-14 |
| LEDs (power, low battery; RGB on Cube/40XX) | GPIO LEDs done; RGB not supported | WP-15 |
| Deep sleep | s2idle only (works, wakes by power key/RTC/lid) | WP-16 |
| Rumble | gpio-vibrator (force feedback) | WP-30 |
| Minimal background activity | radio worker fixed (~0.2 programs/s idle) | WP-21 |
| Automatic SD expansion on first boot | done by 1.x during dual-boot; needed for a 2.x-only image later | WP-05 (later) |
| Update by copying one file | missing | WP-02 |
| SSH/SFTP over Wi-Fi, adb over USB, on by default | adb yes; dropbear present but not started; no mDNS (avahi) | WP-10 |
| `baseos.conf`: hostname, mdns, ssh_password, headphone_pop_fix | parsed by `baseos-config`; only partly applied | WP-10, WP-11 |
| USB storage mode (hold MENU while connecting the cable) | missing; MENU at power-on currently means "start 1.x" (conflict) | WP-03, WP-17 |
| Optional startup sound (`startup_sound.wav`) | missing | WP-18 |
| Frontend choice: custom > Slot > NextUI > spruce; TF2 card first | order done; **TF2 bug**: rcS still tries `mmcblk1p1`, TF2 is `mmcblk2` since the mmc aliases | WP-19 |
| NextUI pending `MinUI.zip` / `*.pakz` installers run first | missing | WP-19 |
| Charger-only start (power off again) | done, untested on hardware | WP-31 |
| Card repair after unclean shutdown | missing | WP-06 |
| Low-battery shutdown | missing | WP-07 |
| spruceOS | cannot start (Mali/fbdev, `/dev/disp`); port 1–2 weeks spruce-side | WP-22 |
| USB host / OTG (docs/08) | reverted (broke adb) | WP-17 |
| NextUI display calibration (colour LUT) | missing (needs KMS gamma/CTM) | WP-23 |
| Time sync (NTP), time zone | `baseos-ntp` runs; RTC fixed on RG SP | WP-30 (check per model) |

## No regression: criteria and harness

Measure on the same unit, same card, same frontend version, same game, same
settings, brightness 50%, Wi-Fi on and connected, against **1.4.0** (and 1.5.3
for reference). A criterion passes when BaseOS 2 is equal or better within the
stated tolerance.

| Metric | Method | Pass |
|---|---|---|
| Power-on to frontend first frame | camera + boot logs (`/run/boot-clock-handoff`, splash kernel log line) | ≤ 1.4.0 |
| Return from game to menu | camera, 5 runs | ≤ 1.4.0 + 5% |
| Game speed and audio | per game/core/shader matrix (v2/notes/performance-slot.md method): fps within 0.1% of core rate, zero steady audio starvation | all cases pass |
| UI frame pacing (menu scroll) | Slot/NextUI frame trace, 60 s scroll | no more dropped frames than 1.4.0 |
| Input latency | camera at 240 fps if available, else frame trace | ≤ 1.4.0 + 1 frame |
| Battery while playing | charger unplugged, 30 min Tetris DX (mGBA) + 30 min a GBA game, `current_now`/`voltage_now` every 10 s | mean power ≤ 1.4.0 + 3% |
| Battery while asleep | charger unplugged, 8 h asleep, capacity before/after | ≤ 1.4.0 |
| Idle (menu, screen on) | 10 min, power as above | ≤ 1.4.0 + 3% |
| Thermals | 30 min GBA with the "authentic" shader, max SoC zone | ≤ 1.4.0 + 2 °C |
| Sleep / wake | power key to screen off / on, camera | ≤ 1.4.0 |
| Wi-Fi | connect time after wake; iperf3-style throughput if tool available | ≤ 1.4.0 / ≥ 1.4.0 − 10% |
| Background activity | new processes/s (`/proc/loadavg` last pid), idle and in game | ≤ 1.4.0 |

WP-20 builds the harness; WP-21 runs it and fixes regressions.

## Work packages

### Phase 1: dual-boot, install, update, safety (blocks everything users touch)

**WP-01 Persistent OS choice in the boot firmware (H, build + device).**
Today the RTC register pair at `0x07000138/13c` is a one-shot "try BaseOS 2"
flag, lost when the power is removed on some units. Add a persistent preference
read by the BaseOS 2 firmware before choosing: e.g. one 512-byte record in a free
area of p4 (document the exact LBA) with magic, version, preferred system (1 or 2)
and a checksum; the RTC pair stays as the one-shot override. Unknown or damaged
record = start 1.x (safe). Both systems must be able to write it: a `baseos boot
1|2` change in 2.x and an equivalent tool in 1.x (`native-health` or a new
command). Read `v2/notes/firmware-all-models.md`, `v2/notes/firmware-rgsp.md`,
`v2/firmware/`. Accept: offline chain tests for all preference/override
combinations on all eleven targets; on the RG SP, cold power-off/on keeps the
chosen system both ways; v2a and v2-rgsp stay buildable byte-identically when the
feature is off.

**WP-02 Installer and updater as "copy one file" (M, build + device).**
1.4.0 updates by copying `<model>.bosupd` to the frontend card. Define a BaseOS 2
package (`<model>-v2.bosupd` or similar) holding firmware, payload and root image
with hashes, and make (a) BaseOS 1.x install it (needs a 1.x point release,
WP-04) and (b) BaseOS 2 update itself from it at start-up, verifying hashes, with
a one-try fallback to the previous BaseOS 2 or to 1.x on failure. Keep power-loss
safety: write order as in `work/v2/firmware/v2a/INSTALL.md`. Delete the package
after success. Accept: install from 1.x and update 2.x→2.x on the RG28XX, an
interrupted update (watchdog reset mid-write, simulated) still starts a working
system.

**WP-03 Where the BaseOS 2 root lives during dual-boot (M, design + build).**
Today it sits in 1.x's spare A/B slot (the first stage picks the squashfs slot),
which 1.x updates would overwrite. Decide and implement: recommended is the root
image as a file on the card's visible FAT partition (e.g. `/.baseos2/root.sqfs`),
loop-mounted by the first stage, so updates are file copies and 1.x keeps its A/B
slots. Handle: mounting the FAT partition once (first stage mounts it, rcS reuses
it), clean shutdown (remount read-only), file fragmentation (copy contiguously,
check read speed), and the prefetch (`v2/os/baseos-prefetch.c`). Accept: boot
time within 50 ms of today on the RG SP; a 1.x update no longer touches BaseOS 2.

**WP-04 BaseOS 1.x transitional release (M, build).** A 1.5.x point release that
knows about BaseOS 2: installs the WP-02 package, offers "start BaseOS 2" and
"always start BaseOS 2 / 1.x" (WP-01 record) through the NextUI Tools paks it
already ships, keeps 1.x's own fast boot when BaseOS 2 is not installed, and
restores it on uninstall (removes BaseOS 2 files, writes 1.x's own boot files from
its release, never from a backup). Accept: offline image tests in the 1.x test
suite (`tests/`).

**WP-05 First-boot expansion for a 2.x-only image (S, later).** Not needed while
1.x installs BaseOS 2; plan only.

**WP-06 Card repair at start-up (S, build + device).** If the card's FAT dirty
flag is set (or after an unclean shutdown), run `fsck.fat -a` (and exFAT
equivalent) before mounting, in the background when possible, logging to
`/data`. 1.4.0's behaviour (`git show v1.4.0:overlay/usr/sbin/`… repair) is the
model. Accept: a unit reset with the card mounted comes back clean; start-up time
unchanged when the card is clean.

**WP-07 Low-battery shutdown (S, build + device).** In `baseos-events`: at the
battery level/voltage 1.4.0 uses (check 1.x and NextUI behaviour), emit
`battery_percent=… low=1` early and power off cleanly (via init, so frontends
save) at the critical level when not charging. Accept: simulated with a test
hook; real discharge test by the owner (WP-31).

### Phase 2: feature parity

**WP-10 SSH/SFTP, mDNS, hostname (S).** Start dropbear (present in the root) and
an mDNS responder (add avahi or a tiny responder) after the frontend launch,
applying `baseos.conf` (`hostname`, `mdns`, `ssh_password`) exactly like 1.4.0.
Host keys on `/data`. Accept: `ssh root@<hostname>.local` and sftp work on the
RG SP over Wi-Fi; no effect on boot time.

**WP-11 Speaker and headphone pop fixes (M, device).** Port 1.4.0's speaker pop
fix (always on) and optional `headphone_pop_fix` (see `v1.4.0:docs/05-runtime-power-network.md`
and `src/h700-speaker-amp/`) to the mainline codec (PA GPIO timing, DAPM).
Accept: no audible pop on start, sleep/wake, headphone plug/unplug (owner listens,
or record with the iPhone mic via `v2/dev/tone.py`).

**WP-12 Analog sticks (M, build; device later).** Check what NextUI, Slot,
RetroArch-style cores and spruce expect for sticks on stock/1.x (same input
device as the buttons, or a separate one; axis codes and ranges). Make BaseOS 2
match the stock input ABI where frontends depend on it. Accept: documented ABI
match; tester verification on a stick model (WP-30).

**WP-13 Bluetooth audio end-to-end (M, device + owner).** Pair, connect and play
through a headset with `baseos bt` and both frontends; automatic switch back to
speaker on disconnect; volume keys control the headset. Accept: owner test with
a real headset; no audio gap longer than 1 s on switch.

**WP-14 HDMI (H, device + owner).** Take `v2/kernel/hdmi-patches/` and
`v2/notes/hdmi.md`, merge into the daily kernel behind a working
`baseos display output lcd|hdmi|auto`, and make NextUI/Slot follow the output
change (contract event). Accept: owner test on a TV: picture, sound, hot-plug,
back to panel; no change to panel boot time.

**WP-15 LEDs (M).** Power and low-battery LEDs on every model; RGB LEDs on
CubeXX/40XX as 1.4.0 supports them (find the stock MCU protocol). Expose through
the Linux LED class. Accept: per-model table in `v2/kernel/MODELS.md`.

**WP-16 Deep sleep (H, device).** Investigate PSCI system suspend with the
mainline TF-A for H616/H700 versus today's s2idle; measure sleep power (WP-20)
first. Implement only if it beats s2idle on battery without breaking wake by
power key, lid and RTC. Accept: 8 h sleep drain ≤ 1.4.0.

**WP-17 USB storage mode and USB host (H, device).** (a) USB storage: same
gesture as 1.4.0 (hold MENU while connecting the cable): unmount the card
partition and export it through a configfs mass-storage gadget; restart restores
normal mode. This moves the "start BaseOS 1.x" gesture: replace "hold MENU at
power-on" with the WP-01 preference plus a different gesture (propose one that
1.4.0 does not use). (b) USB host: rework per `v2/notes/usb-host.md`: keep the
MUSB notifier for the device role, route only explicit host mode to EHCI/OHCI,
auto-return to adb. Accept: from a `--dev` payload, adb never lost; storage mode
mounts on a Mac; a USB keyboard or DAC works in host mode.

**WP-18 Startup sound (S).** Play `startup_sound.wav` from the root of TF1's
visible partition during start-up, as 1.4.0 (short WAV only), without delaying
the frontend. Accept: plays on the RG SP; handoff time unchanged.

**WP-19 Frontend start parity (S).** Fix TF2 detection (`mmcblk2p1` after the mmc
aliases; probe by content, not by number), and run NextUI's pending `MinUI.zip`
and `*.pakz` installers before frontend selection exactly as 1.4.0's
`frontend-session` does (`git show v1.4.0:overlay/usr/sbin/frontend-session`).
Accept: shell tests like 1.x `tests/test-frontend-session.sh` adapted to v2.

**WP-22 spruceOS (H, frontend-side).** Follow `v2/notes/spruce-audit.md`: minimal
spruce changes plus any BaseOS-side standard interfaces it needs. Separate
branch in a spruce fork. Accept: spruce menu, a game with sound, sleep, power off
on a BaseOS 2 unit.

**WP-23 NextUI display calibration (M).** Implement the colour calibration NextUI
does through `/dev/disp` on stock using KMS gamma LUT/CTM, either as a `baseos
display` command or by NextUI using the KMS property directly (minimal NextUI
change). Accept: calibration changes the panel colours on the RG28XX.

### Phase 3: performance and validation

**WP-20 Benchmark harness (M, build + device).** Scripts in `v2/bench/` that run
the "No regression" table over adb on BaseOS 2 and on 1.4.0 (and 1.5.3), using
only what both systems have (busybox, sysfs, the camera). Store results as JSON
under `work/v2/bench/` and a summary in `v2/notes/bench.md`. Start from the Slot
method in `v2/notes/performance-slot.md`. Includes how to run 1.4.0 on the same
unit (1.x boot via the dual-boot preference).

**WP-21 Regression hunt (H, device).** Run WP-20 on the RG28XX (NextUI) and RG SP
(Slot); fix every failing row in BaseOS first (kernel config, cpufreq/devfreq
policy, Mesa, background activity, I/O), in the frontend only if the cause is its
BaseOS 2 port. Open items already known: Slot "authentic" shader keeps the GPU at
100% (power); NextUI never profiled on BaseOS 2; battery and sleep power never
measured.

**WP-30 Per-model bring-up (M, testers).** For each of the nine untested models:
install via WP-02, run a checklist (picture, every button and stick, audio,
headphones, Wi-Fi, Bluetooth, battery, sleep/wake, lid, LEDs, rumble, TF2, power
off/on, charger start) and fix DT/firmware issues found. Needs one tester unit
per model; produce `v2/notes/models-tested.md`.

**WP-31 Owner checklist on the RG SP and RG28XX (owner).** Lid close/open,
charger start, OS switch gesture, headphones, Bluetooth headset, several hours of
play, battery percentage and charging, unplugged battery tests for WP-20.

### Phase 4: release

**WP-40 Release engineering (S).** Merge `feature/h700-mainline` into `main`
(clean history, no stray `experiments/` edits), version the BaseOS 2 beta, build
every model's package from clean `main` only, publish as a pre-release, and write
the wiki install guide in the simple style of the 1.x guide (technical detail in
`v2/notes/`).

## Order and dependencies

1. Now, in parallel: WP-19 (TF2 bug), WP-06, WP-07, WP-10, WP-18, WP-20, WP-03
   design, WP-01.
2. Then WP-02 and WP-04 (need WP-01 and WP-03), WP-17, WP-11, WP-12, WP-15.
3. Then WP-21 (needs WP-20 and the owner's unplugged runs), WP-13, WP-14, WP-16,
   WP-23, WP-22.
4. Beta candidate: WP-30 with testers, WP-31, WP-40.

## Open decisions for the owner

- WP-03: root image as a file on the FAT partition (recommended) versus keeping
  the spare A/B slot.
- WP-17: the new gesture for "start BaseOS 1.x once", since MENU belongs to USB
  storage in 1.4.0.
- WP-22: spruce in the beta or after.
- Testers for the nine models without a unit in the lab.
