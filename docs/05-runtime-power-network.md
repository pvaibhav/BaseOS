# 05. Runtime: boot timing, power/sleep, network

## 1. Boot timing

`/run/boot-frontend-exec` records kernel uptime at the first frontend handoff.
See [boot performance](09-boot-profiling.md) for measurement instructions and
the acceptance ceiling.

`rcS` loads `mali_kbase.ko` in the background so it overlaps storage and
service startup. `frontend-session` waits a bounded time for `/dev/mali0`
before handoff. Ordinary boots leave the bootloader logo untouched until the
frontend renders its first frame.

## 2. Deep sleep

The frontend enters suspend-to-RAM by writing `mem` to `/sys/power/state`.
`alsactl` saves and restores the mixer across sleep.

BaseOS applies the stock Super Standby value (`16`) when the SP-only
`axp2202-battery/os_sleep` attribute exists. NextUI also applies it before
suspend. This value stops the USB host controllers and removes the hall sensor
from the wake set, so POWER wakes the device but opening the lid alone does
not. Non-SP kernels do not expose `os_sleep` and use the full USB-stop path.

## 3. Wi-Fi

BaseOS owns interface startup. Like stock, `rcS` loads `rtl_btlpm.ko` during
early boot. A background task in `rcS` then asks the vendor WLAN controller to
discard any SDIO card it enumerated before the kernel's unused-regulator cleanup
turned off that card's supply. The task waits up to one second for the stale
card to disappear, then loads `8821cs.ko`. Without this ordering, the driver
probe on H700 devices can fail with `-123` and never create `wlan0`. Only
delaying module insertion does not prevent the failed probe. Radios that are
already powered, and kernels without these controls, skip this step.

Some radios only reset properly after a long power-off. The kernel disables
the WLAN supply (`axp2202-cldo4`) at about 1.85 s, and the driver powers it
back on about 0.6 s later. On those units, the fresh card answers with a
corrupt SDIO ID (`020C:C821` instead of `024C:C821`, so no driver binds) or does
not answer at all, on every boot. Stock's own boot log shows its driver loading
3.8 s after the cut, which is why the same units work on stock. A 20 s
power-off recovered such a unit at runtime, and 1 s did not.

When the first load produces no `wlan0` within 2 s, the task unloads the driver,
which powers the radio off. It keeps the radio off for 5 s, loads the driver
again, and creates `/data/wifi-slow-radio`. On later boots, that unit holds the
first load until about 6.85 s uptime instead of failing first. Units whose first
attempt works, which is the common case, are unaffected. Delete the marker to
return a unit to the fast path. Kernel messages prefixed `baseos: wifi:` record
which path ran.

The same background task waits for `wlan0`, then unblocks the radio and brings
the interface up. None of these waits delay frontend handoff. Frontends manage
network credentials, association and DHCP. A frontend that starts before the
interface appears must wait for it. NextUI's H700 Wi-Fi script waits up to 25 s.

The `systemctl` compatibility shim implements a synchronous
`stop wpa_supplicant`, including interface service names, with a bounded
two-second wait. NextUI's existing restart script can then wait for the old
daemon to clean up its control socket before launching the replacement. When no
supplicant is running, the command returns immediately.

Driver power saving uses the stock default, `rtw_power_mgnt=2`. This can add
latency to intermittent traffic. DHCP uses BusyBox `udhcpc`. Its event script
writes `/run/resolv.conf`, which `/etc/resolv.conf` links to.

### Hostname and mDNS

`baseos-config` reads `baseos.conf` from TF1's visible partition, even when
TF2 holds the frontend. It ignores unknown keys. The
[README settings reference](../README.md#settings) documents the supported
keys, defaults and value syntax.

Normal boot reads the file once. USB-storage mode uses the defaults and does
not open the exported card.

After Wi-Fi gets an IPv4 address, the DHCP hook starts the static Avahi
responder in the background through `baseos-mdns`. It publishes
`<hostname>.local` on `wlan0`, with no service advertisements and no D-Bus
dependency. Renewals reuse the running daemon. When DHCP deconfigures the
interface, the hook reconciles the daemon with the current interface state.
Avahi handles later interface changes, so no extra polling process runs.

On a name collision, mDNS adds a suffix, such as `rg34xxsp-2.local`, without
changing the configured hostname. Set distinct hostnames for stable addresses.
`mdns=false` disables the responder.

### Speaker and headphone pop repair

Each rootfs includes `h700_speaker_amp.ko`, built for its exact vendor kernel.
`rcS` loads it after reading settings and before the frontend starts audio.
The module gates the speaker amplifier around codec power transitions. It also
filters GPIO writes from the jack worker so they cannot turn on an idle
amplifier. Other GPIOs and the vendor's jack detection stay under vendor
control.

`headphone_pop_fix` defaults to `false`. Enabling it keeps the analogue
line-output buffers powered between streams to suppress the headphone exit pop.
This can raise idle power. The buffers are released before suspend and
shutdown. Missing or invalid settings select `false`.
Restart after changing TF1's configuration. The speaker repair is active with
either setting.

The build pins each kernel Image and configuration and checks module symbol
versions and layout. At load time, the module checks kernel identity, codec
widgets and GPIO callbacks before attaching. The build resolves addresses, so
initialization does no symbol searches or settling sleeps. The module cannot be
unloaded because live kernel callbacks reference it. Reboot to replace it. A
different firmware kernel needs a new reviewed profile and hardware validation
before it can be packaged.

Regression tests cover GPIO arbitration and startup order. Hardware acceptance
must also check audible start and stop behavior, headphone insertion and
removal, suspend and resume, and the added boot cost on each target. A
successful cross-build does not prove any of those hardware results.

## 4. Bluetooth audio

`rtk_hciattach` attaches the controller UART. The `systemctl` shim starts D-Bus
and BlueZ on demand, and BlueALSA provides the audio service. Frontend scripts
configure pairing and audio through the vendor-compatible shims. The harvest
omits radio files for profiles without Bluetooth.

See [validation coverage](06-status-and-lessons.md) for hardware checks.

## 5. Power button and shutdown

BaseOS provides `poweroff` and `reboot` wrappers. `baseos-poweroff` creates
`/run/poweroff-requested`, and `baseos-reboot` clears it. Both then call
BusyBox to request shutdown through init.

`rcK` stops other processes, saves entropy, disables swap and flushes writes.
For power-off it calls `axp-off --now`, which requires persistent filesystems
to be read-only before it cuts processor power through the PMIC. Reboot skips
the PMIC cut. If the PMIC helper refuses, shutdown falls back to BusyBox's
kernel path after filesystem cleanup.

Frontends should use the BaseOS commands so that shutdown intent and cleanup
are kept. Calling BusyBox applets directly, or using frontend-specific helpers,
can bypass them. A long power-button press forces a hardware power-off.

Boots that start from the charger use the early charging path before normal
services start; see [charger-only boot](11-charger-only-boot.md).

## 6. USB access

BaseOS starts USB-only adb in the background through `/etc/init.d/dev`.
Connect a data cable before power-on. After disconnecting it, restart with the
cable connected. `/data/no-adb` disables adb.

Holding MENU during startup selects exclusive USB-storage maintenance mode.
This mode exports the whole TF2 card when present, otherwise TF1's data
partition, and keeps the frontend stopped until restart. Eject the drive on the
host before restarting without MENU.

See [USB adb, mass storage and H700 OTG](08-usb-adb-and-otg.md) for gadget
configuration, role handling, storage safety and validation.

## Optional startup sound

Place `startup_sound.wav` at the root of TF1's BASEOS partition. The tested
format is 48 kHz stereo, signed 16-bit PCM WAV. BaseOS bundles no audio file.
Remove or rename the file for a silent startup.

`rcS` starts `baseos-startup-sound` in the background after the audio repair.
The helper reuses TF1's existing mount. When TF2 hosts the frontend, it mounts
TF1 read-only under `/run/startup-sound-tf1` and releases that private mount
after playback. It never searches TF2. A missing or empty file leaves the audio
controls untouched. USB-storage boots skip the helper, and charger-only startup
reaches it only after POWER is accepted.

Playback uses full codec gain and the default ALSA speaker route, lasts at most
30 seconds, and does not delay frontend handoff. The default PCM locks its
routing and digital-volume controls during playback, so a frontend that opens
the same hardware PCM while the sound plays may find it busy. Keep startup
clips short. Diagnostics go to `/run/startup-sound.log`.
