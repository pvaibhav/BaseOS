# BaseOS 2 frontend contract (v1, draft)

BaseOS 2 runs a mainline Linux kernel. It offers frontends one set of
interfaces on every supported handheld: standard Linux interfaces where a
robust standard exists, and the `baseos` command where one does not (policy,
vendor quirks, multi-step workflows). A frontend that also targets the stock
firmware or BaseOS 1.x checks for BaseOS 2 like this and otherwise keeps its
vendor code paths:

```sh
[ -x /usr/bin/baseos ]   # BaseOS 2 or later
```

An optional compatibility layer (not part of this contract) may later run
unmodified BaseOS 1.x frontends by emulating vendor interfaces.

Status marks: **works** (implemented and tested on hardware), **untested**
(implemented, no hardware to test yet), **planned**.

## Conventions

- `baseos AREA ACTION [ARGS]`. Reading commands print `key=value` lines;
  values never contain newlines. Exit status: 0 success, 1 failure,
  2 usage error, 3 not available on this model.
- Percentages are integers 0-100. Times are seconds. Temperatures are
  millidegrees Celsius, as in Linux.
- Commands are safe to call from a frontend at any time; they never block for
  more than a second unless documented (`wifi connect`, `bt pair`).

## Device: `baseos info` (works)

```
model=rg28xx            # BaseOS model id
name=Anbernic RG28XX
baseos=2.0.0
kernel=7.3.1
ram_mb=1024
panel=old               # panel revision chosen by the boot loader
screen=480x640          # native panel size; see display info for rotation
features=...            # space-separated: battery hdmi wifi bt lid sticks rumble leds usb-host tf2
```

## Graphics (works)

- SDL2 (2.28 ABI, the same exports as NextUI's H700 SDL) with the KMSDRM
  backend, in `/usr/lib/baseos2/`. OpenGL ES through Mesa (Panfrost): ES 3.1
  at most, so request 3.2 and fall back to 3.0/3.1. `libEGL.so.1`,
  `libGLESv2.so.2` and `libgbm.so.1` are Mesa, in the system library path.
- A frontend that bundles its own SDL puts `/usr/lib/baseos2` first in
  `LD_LIBRARY_PATH` and sets `SDL_VIDEODRIVER=kmsdrm`.
- Panel orientation: `SDL_ROTATION` (0-3, quarter turns), as on stock.
- The first frame takes over from the boot logo without the screen blanking.

## Display: `baseos display` (works, HDMI planned)

```
baseos display info               # width height rotation refresh_mhz output
baseos display brightness [N]     # print or set; 0 turns the backlight off
baseos display power on|off       # screen off without sleeping (e.g. music)
baseos display output lcd|hdmi|auto   # planned: models with HDMI
```

## Audio: ALSA + `baseos audio` (works for speaker/headphones; USB untested; BT planned)

- Play through the ALSA `default` device. BaseOS points it at the active
  output. When the output changes, `baseos events` reports `audio_output=...`;
  frontends that hold the device open reopen it.
- Outputs: `speaker`, `headphones` (3.5 mm jack; the kernel mutes the speaker
  while headphones are in), `usb` (USB audio class device, needs USB host
  mode), `bluetooth` (A2DP through bluealsa).
- `auto` (default): bluetooth if connected, else usb if present, else
  headphones if plugged in, else speaker.

```
baseos audio status          # output=… outputs=… volume=… muted=…
baseos audio output NAME|auto
baseos audio volume [N]      # of the active output
baseos audio mute on|off
```

## Power: `baseos power` (works)

```
baseos power status          # battery_percent battery_status battery_mv
                             # battery_ma usb_power boot_reason
baseos power off             # clean shutdown, then the power chip turns off
baseos power restart         # clean shutdown, then a full restart
baseos power sleep [--wake-in S]   # suspend; wake on power key, lid or timer
```

- `poweroff` and `reboot` do the same as `power off|restart`. Both stop the
  frontend, unmount the cards and move the CPU to its boot speed and voltage
  first, so the next start never runs on a lowered voltage (the vendor
  firmware's "stuck after restart" problem).
- Battery data is also in `/sys/class/power_supply/axp2202-battery`
  (`capacity`, `status`, `voltage_now`, `current_now`) and USB in
  `axp2202-usb/online`, the same names as the stock firmware.
- Wake sources: power key, lid (models with a lid), RTC timer.
- `boot_reason`: `button`, `usb` (plugged in while off), `charger`,
  `restart`, `unknown`.

## Time and alarms (works)

- System time and `hwclock` as usual; the RTC keeps time while off.
- `baseos rtc wake S|off`: wake the device S seconds from now (from sleep,
  and from power-off on models whose power chip supports it: untested).
- Time zone: `timedatectl set-timezone ZONE` (BaseOS command, as in 1.x).

## Performance and telemetry: `baseos perf` (works)

```
baseos perf profile [powersave|balanced|performance|max]
baseos perf status    # cpu_mhz cpu_governor cpu_load gpu_mhz gpu_load
                      # cpu_temp gpu_temp battery_mw
```

- `balanced` (default): CPU follows load (schedutil). `performance`: CPU at its
  fastest safe speed. `max`: also raises the GPU to its fastest speed
  (planned: GPU frequency scaling). `powersave`: CPU capped at a low speed.
- For a once-per-second HUD read the standard files directly instead of
  running a command: `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`,
  `/sys/class/thermal/thermal_zone*/{type,temp}`,
  `/sys/class/devfreq/*gpu*/cur_freq` (when GPU scaling is enabled).

## Input (works)

- evdev. Find devices by name or capability, never by event number.
  - Pad: `ANBERNIC-keys`, the stock layout (D-pad as `ABS_HAT0X/Y`, buttons
    `BTN_*` as on stock, volume keys `KEY_VOLUMEUP/DOWN`).
  - Power key: `axp2202-pek`, only `KEY_POWER`.
  - Headphones: a switch device with `SW_HEADPHONE_INSERT`.
  - Planned per model: lid (`SW_LID`), analog sticks (`ABS_X/Y/RX/RY`),
    rumble through the standard force-feedback API (`EV_FF`, so SDL haptics
    works).
- LEDs (planned per model): `/sys/class/leds/*`.

## Networking: `baseos wifi`, `baseos bt` (planned)

```
baseos wifi on|off|status|scan
baseos wifi connect SSID [PASSWORD]  |  forget SSID  |  networks
baseos bt on|off|status|scan
baseos bt pair|connect|disconnect|forget ADDRESS  |  devices
```

- Wi-Fi through wpa_supplicant; known networks are kept on the internal
  data partition, so they survive OS updates.
- Bluetooth through bluez; audio devices through bluealsa (see Audio).

## USB: `baseos usb` (works for adb; host untested)

```
baseos usb mode [adb|host|storage|none]   # print or set the USB-C role
```

- `host` powers the port and accepts controllers, audio devices and storage.

## System services (works)

- The D-Bus system bus is running (`/run/dbus/system_bus_socket`).
- The kernel's random number pool is ready early in boot (hardware generator).
- `/etc/machine-id` is stable across restarts and updates.

## Events: `baseos events` (works)

Prints one line per change until killed, `name=value` plus extra fields:

```
headphones=1
usb_power=0
battery_percent=14 low=1
audio_output=usb
lid=closed
wifi=connected ssid=…
```

## Choosing the system: `baseos boot` (works)

- `baseos boot 1` makes the next start BaseOS 1.x; `baseos boot 2` BaseOS 2.
  Restart afterwards.
