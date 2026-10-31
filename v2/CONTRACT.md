# BaseOS 2 frontend contract (v0, draft)

BaseOS 2 runs a mainline Linux kernel. Frontends talk to it through the
interfaces below, mostly standard Linux ones. A frontend that also targets the
stock firmware or BaseOS 1.x checks for BaseOS 2 and uses these instead of the
vendor interfaces:

```sh
if [ -x /usr/bin/baseos ]; then   # BaseOS 2 or later
```

An optional compatibility layer (not part of this contract) may later let
unmodified BaseOS 1.x frontends run by emulating vendor interfaces.

## Graphics

- SDL2 (2.28 ABI, the same exports as NextUI's H700 SDL) with the KMSDRM video
  backend and OpenGL ES through Mesa (Panfrost). OpenGL ES 3.1 at most: ask for
  3.2 and fall back to 3.0/3.1.
- The system SDL is in `/usr/lib/baseos2/`. A frontend that bundles its own SDL
  puts that directory first in `LD_LIBRARY_PATH` and sets `SDL_VIDEODRIVER=kmsdrm`.
- `libEGL.so.1`, `libGLESv2.so.2`, `libgbm.so.1` are Mesa, in the system library path.
- Panel rotation: `SDL_ROTATION` (0-3, quarter turns) as on the stock firmware.

## Audio

- ALSA, default device. `baseos volume` prints 0-100; `baseos volume N` sets it.

## Display

- `baseos brightness` prints 0-100; `baseos brightness N` sets it (0 = backlight off).

## Power

- `poweroff` and `reboot`: clean shutdown through init (card and data unmounted).
- `baseos sleep`: suspend until the power key (or lid) wakes the device.
- Battery: `/sys/class/power_supply/*` with `type` `Battery` (capacity, status)
  and `USB` (online). On H700 devices these are named `axp2202-battery` and
  `axp2202-usb`, as on the stock firmware.

## Input

- evdev. The pad keeps the stock layout and name (`ANBERNIC-keys`). The power key
  is a separate device with only `KEY_POWER` (`axp2202-pek`). Find devices by
  name or capability, not by event number.

## Choosing the system

- `baseos boot 1` makes the next start BaseOS 1.x; `baseos boot 2` BaseOS 2.
  Restart with `reboot` afterwards.

## Not yet defined

Wi-Fi, Bluetooth, HDMI, rumble, LEDs, lid, analog sticks: to be added as the
corresponding models are brought up.
