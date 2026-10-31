# RG SP mainline kernel bring-up

Build-only implementation. “Done” below means DT/driver wiring is present and
compiled, not hardware qualification. No handheld was accessed. Full stock
parity is still blocked by HDMI and needs device testing for all new paths.

## Stock evidence

The RG SP source image is RGSP-V1.0.1-BASE-20260624-mod-v4.0.0.img,
recorded in `work/rgsp/source.json` in the original BaseOS checkout. The stock
DTB used here is the 137564-byte FDT at byte 175486976 of
`work/rgsp/boot-prefix.img` (inside the boot partition). It was read through a
read-only container mount. References below are node/property names in that
DTB. BaseOS 1.x `kernel/boards/input-abi.json`, `devices.json`, and
`src/h700-speaker-amp/profiles.json` supply the model and input cross-checks.
RG SP is distinct from RG35XX SP: in particular stock has no Wi-Fi reset GPIO.

| Feature | Stock evidence | Mainline driver / DT | Status |
| --- | --- | --- | --- |
| LCD | `lcd0`: rg34xxsp_v1, 720×480, 26 MHz nominal, 820×536 totals, RGB; PI9/10/8 SPI, PI14 reset, PI15 power | Existing `panel-baseos-h700` RG SP table, variant old; inherited SPI/DPI graph, RG SP clock selector 2 | Done; loader must supply `/chosen/baseos,boot-display` for adoption. Cold initialization and sleep/resume need testing |
| Backlight | PWM0, 50 kHz, positive polarity | Existing PWM and pwm-backlight; 20000 ns period | Done |
| Pad and volume | `gpio_keys`, GPIO polling; PA0–12 and PE0–3, 17 buttons/axes | Inherits RG28XX `ANBERNIC-keys` with the exact stock codes, ABS_HAT0X/Y and volume keys; no ADC buttons or sticks | Done |
| Lid | PMIC powerkey `hall_key`: PE7, pulled up, active low | gpio-keys, EV_SW / SW_LID; wake on opening | Done; deep suspend/wake depends on the existing firmware path and needs testing |
| Power key / battery / USB input | AXP2202 at R-I2C 0x34; 3500 mAh, 1500 mA charging, 4100 mV charge voltage, no thermistor; USB adapter current 2500 mA | Existing BaseOS AXP717 drivers and overlay, with RG SP battery capacity; existing axp2202-pek / axp2202-battery / axp2202-usb names | Done; verify charge/current readings before extended testing |
| Audio / speaker amp | `codec`: H616 codec, CLDO1 amp supply, ALDO4 AVCC 1.8 V, PI5 active-high PA, PI3 headphone detect | Existing sun4i-codec H616 support and BaseOS jack handling; shared overlay routes LINEOUT to speaker/headphones, CLDO1 3.3 V | Done; jack reports SW_HEADPHONE_INSERT |
| Wi-Fi | stock `8821cs.ko`; `wlan_busnum=1`, CLDO4 supply, PG10 32 kHz; no chip_en/power_en GPIO; SDIO PG0–5 at 1.8 V | rtw88_8821cs modules, MMC1 at conservative 50 MHz, ALDO4 1.8 V I/O, CLDO4 3.3 V, clock-only mmc-pwrseq | Needs firmware file; in-band SDIO IRQ, no invented PG18 reset or PG15 host-wake |
| Bluetooth | stock `rtlbt/rtl8821c_fw` and config; UART1 PG6–9 with RTS/CTS; PG19 reset/enable, PG17 device wake, PG16 host wake | hci_uart H5/serdev + btrtl modules, rtl8821cs-bt compatible | Needs firmware file; baud rate and wake need testing |
| HDMI video / audio / CEC | `hdmi@06000000`, hdmi_used=1, vcc-hdmi/vdd-hdmi; stock HDMI codec and AHUB path | sun4i DRM supports other SoCs, but this tree has no H616/H700 DW-HDMI/PHY match or complete HDMI/TV DT graph | Needs driver work; no HDMI nodes enabled or driver port attempted |
| Rumble | battery `moto_pwm_gpio`: PH2, active high | gpio-vibrator module, standard EV_FF / FF_RUMBLE, on/off motor | Done; magnitude is on/off, not variable PWM strength |
| LEDs | `work_led` PI12, `low_pwr_led` PI11; boot-init sets PI12 on, PI11 off | gpio-leds, green:power / green:status; retain boot state | Done; PMIC charge LED stays managed by existing PMIC state, no separate charge-LED class driver added |
| USB device | USB0, PI4 ID detect, PMIC VBUS sensing | sunxi MUSB dual-role, AXP USB power-supply notifier | Done; gadget path and cable transitions need testing |
| USB host / VBUS | USB0 `usb_drv_vbus_gpio` PI16; PMIC also describes PH12 ID drive / PH13 detect and Type-C support | RG SP overlay adds boost 5.0–5.2 V → PI16 VBUS switch, PI4 ID sense, EHCI0/OHCI0 modules | Done for GPIO/PHY path; automatic Type-C CC/PMIC role policy needs driver work if PI4 does not reflect role on this board. Unused stock USB1–3 controllers are not enabled |
| TF1 | MMC0, PF0–5, stock non-removable 50 MHz, no voltage switch | Existing sunxi-mmc and retained 3.3 V supply | Done; unchanged RG28XX path |
| TF2 | MMC2 four-bit PC5/6/8/10/13/15, PE22 card detect, PE4 power, ALDO1 I/O; stock also specifies broken-cd | sunxi-mmc, dedicated six-pin group, PE4 fixed regulator, ALDO1 3.3 V, 50 MHz; use PE22 detection | Done; test empty/inserted/removed cards; stock broken-cd is a reason to check detection carefully |
| RTC | R-I2C `rtc@51`, nxp,pcf8563 | rtc-pcf8563 module; on-chip RTC CCU still supplies radio clock | Done; external RTC alarm has no proven interrupt/wake wiring |
| CPU / GPU / remaining rails | stock DCDC1 CPU, DCDC2 GPU/system, DCDC3 DRAM; boot-initialized rails | Inherited CPU DVFS and retained GPU rail/OPPs; shared PMIC overlay changes only named consumers | Done; other rails retain loader values rather than imposing guessed voltages |

## Implementation and boot behavior

`sun50i-h700-anbernic-rgsp-baseos.dts` includes the existing RG28XX board DTS
and overrides the model, panel, framebuffer geometry and extra peripherals.
This deliberately retains the loader's pinned display phandles. The RG28XX
DTS and its overlay source are unchanged. `drivers/of/baseos-overlay.c`
selects the RG SP overlay only for `anbernic,rgsp`; it includes the shared
RG28XX power/audio overlay and adds model-specific nodes. PMIC nodes remain
hidden from the stock boot loader.

CLDO4 remains on because Wi-Fi and Bluetooth share it and the Realtek serial
binding has no supply property. Removing the Wi-Fi module must not cut power
to active Bluetooth. The 32 kHz clock pwrseq has no added reset delay. MMC
hosts remain built in and enumerate asynchronously; the network firmware
requests happen only when optional modules load. There is no user-helper
firmware fallback timeout. Module loading policy is outside this kernel task.

## Configuration

The resolved configuration is exported to `v2/kernel/config`.

- Built in: KEYBOARD_GPIO (interrupt-driven lid alongside existing polled pad),
  SERIAL_DEV_BUS / SERIAL_DEV_CTRL_TTYPORT, PWRSEQ_SIMPLE, NETDEVICES, WLAN,
  WIRELESS, PACKET, USB core and MUSB dual-role. MUSB gadget-only becomes
  dual-role; the RG28XX DT still requests peripheral mode.
- Modules: CFG80211, MAC80211, RFKILL, RTW88 / RTW88_CORE / RTW88_SDIO /
  RTW88_8821C / RTW88_8821CS; BT / BT_RTL / BT_HCIUART (H5, Realtek, serdev),
  BT_RFCOMM / BT_BNEP; INPUT_GPIO_VIBRA / INPUT_FF_MEMLESS;
  RTC_DRV_PCF8563; USB_EHCI_HCD / USB_EHCI_HCD_PLATFORM and USB_OHCI_HCD /
  USB_OHCI_HCD_PLATFORM.
- Kconfig selects firmware loading, wireless/BT crypto dependencies, keys and
  signed regulatory database verification. No other wireless chip driver is
  selected. Signed regdb support requires native OpenSSL for build helpers;
  build.sh clears the inherited frontend pkg-config sysroot/path for them.

## Firmware and modules for later integration

Nothing is copied into `v2/firmware` or onto a device. Install the module tree
from `work/v2/kernel/modules/lib/modules/<kernelrelease>` with the matching
Image when preparing a test OS. Load `rtw88_8821cs`, `hci_uart`, `gpio_vibra`,
`rtc_pcf8563` and, for USB host, `ehci_platform` / `ohci_platform`; dependencies
are part of the exported module tree. Userspace still needs its own wireless
and Bluetooth services; those are outside this task.

Files below are paths relative to `/lib/firmware`:

| File | Source |
| --- | --- |
| `rtw88/rtw8821c_fw.bin` | linux-firmware, rtw88 directory. Stock vendor Wi-Fi module embeds its own firmware; do not substitute the vendor .ko |
| `rtl_bt/rtl8821cs_fw.bin` | linux-firmware, rtl_bt directory |
| `rtl_bt/rtl8821cs_config.bin` | linux-firmware, rtl_bt directory; required by btrtl |
| `regulatory.db`, `regulatory.db.p7s` | signed wireless-regdb distribution; keep the matched pair |

Upstream sources: [linux-firmware](https://gitlab.com/kernel-firmware/linux-firmware),
[Realtek Bluetooth files](https://gitlab.com/kernel-firmware/linux-firmware/-/tree/main/rtl_bt),
[wireless-regdb](https://www.kernel.org/pub/software/network/wireless-regdb/).
The exact requests are declared in `rtw8821c.c`, `btrtl.c` and
`net/wireless/reg.c` in the built tree.

The stock rootfs has `/usr/lib/firmware/rtlbt/rtl8821c_fw` and
`rtl8821c_config` as a board-specific fallback to investigate if upstream
Bluetooth firmware fails. They are vendor names, not the requested mainline
paths; compatibility has not been established. Do not substitute unrelated
stock rtl8723b files or overwrite board addresses/configuration blindly.

## First device checks

1. Boot the matching Image and rgsp.dtb with the existing RG SP loader.
   Check overlay success, 720×480 picture, correct colors/orientation,
   first-frame takeover and brightness. The tree defaults to old; the loader
   may select boe using the same variant property when appropriate.
2. Check the three axp2202 ABI names, battery voltage/current/capacity, power
   key and clean shutdown/restart. Check speaker at low volume first, then
   jack insertion/removal and automatic speaker muting.
3. Check every pad button and both hat axes, volume, SW_LID open/close and
   LEDs. Then test a short FF_RUMBLE effect. Verify lid wake only after ordinary
   boot and power are reliable.
4. Check TF1 and TF2 enumeration, TF2 insertion/removal and reads. Test USB
   device first, then host with a low-power peripheral; verify VBUS switches
   off when leaving host mode and cable role changes are reflected correctly.
5. Supply firmware and modules, then test Wi-Fi scan/association/traffic,
   Bluetooth discovery/pairing/audio, and coexistence. Finally test suspend,
   lid/power-key wake, display/audio restoration and radio reconnection.

HDMI is not a useful device test for this build: the driver path is absent.

## Build validation

Kernel commit: `8706b8df27987b943924d89c6b7aaff1f47cde9d` (author Prashant Vaibhav).
Kernel release: `7.3.1-baseos2+`. `sh v2/kernel/build.sh` completed with Image,
modules and both DTBs, and exported the final config and patch series.
The container needed its native `kmod` package for depmod; no host package
was installed. The kernel worktree is clean. No repository commit or push
was made.

Checks against the produced artifacts:

- Both board overlays apply with fdtoverlay.
- All 17 RG SP stock GPIO pin/code/type/value input tuples match.
- The merged DT resolves the RG SP panel, SW_LID, 3500 mAh battery and OTG role.
- RG28XX DTB SHA-256 matches the pre-change DTB exactly.
- Module dependency indexes resolve rtw88_8821cs, hci_uart, gpio_vibra,
  rtc_pcf8563, ehci_platform and ohci_platform.
- Final build has no RTC address-cell or missing-depmod warning.

Outputs: `work/v2/kernel/Image` (9891848 bytes),
`work/v2/kernel/rg28xx.dtb` (37454 bytes),
`work/v2/kernel/rgsp.dtb` (39254 bytes), and
`work/v2/kernel/modules/`. These are build artifacts, not a device install.
