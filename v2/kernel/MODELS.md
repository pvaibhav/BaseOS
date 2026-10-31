# Remaining H700 models — build-only, all untested

Every status below is source/build validation only. No handheld was accessed.
This is not full stock parity: HDMI has no H700 driver; RGB lighting needs a
controller/protocol implementation; analog input uses a separate event device.
Power, charging, display adoption, suspend/resume, audio, radios and storage
all need real device testing. No files were installed on a device.

The new board files are `arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-<id>-baseos.dts`
in the kernel tree. Each includes `sun50i-h700-anbernic-baseos-radio.dtsi`,
which retains the RG28XX display phandles, TF1 aliases and GPU 432–648 MHz
OPPs on the retained 0.94 V rail. Analog boards also include
`sun50i-h700-anbernic-baseos-sticks.dtsi`. Each `drivers/of/baseos-<id>.dtso`
includes the existing RG SP overlay and supplies the stock battery capacity.
Exact compatible selection in `baseos-overlay.c` keeps PMIC nodes hidden from
the stock loader until Linux applies the overlay. No RG28XX/RG SP source was changed.

The evidence below comes from the **normal boot partition kernel DTB**, read
from each original checkout `work/<id>/boot-prefix.img`, mounted read-only.
The images also contain a bootloader DTB and a shared recovery DTB; neither
was used as the final kernel input comparison. Offsets, lengths and SHA-256
are recorded per model below. Other sources are the matching stock
`rootfs.tar`, `devices.json`, `kernel/boards/input-abi.json`, speaker amp
profiles and upstream RG35XX H/Plus/SP DTS files in this kernel.
The prior gap matrix uses different DTB hashes; it was used for context only.

Panel revision defaults to old except CubeXX (boe only). The loader must
write the actual old/boe selection into `baseos,panel` and supply
`/chosen/baseos,boot-display` for adoption. Linux does not probe the PE6 strap.
Existing per-model driver tables supply the actual timing and initialization.

All models have stock GPIO pad/volume input, not ADC buttons. Hats keep
ABS_HAT0X/Y and stock face/shoulder/menu codes rather than upstream semantic
renaming. Stick clicks retain stock 0x139/0x13c. Analog axes use mainline
`adc-joystick` + `io-channel-mux` + `gpio-mux` + H616 GPADC, channel 0,
PI1/PI2 active-low selection, 100 us settling, 16 ms polling, 0–4096 range,
32 flat/fuzz. PI0 is held high per stock `amux-en-gpios`. PE11 is left as an
input (stock ADC input configuration), not driven as an invented enable.
There is no evidence that these sticks use a serial MCU. Axis direction
comes from the existing input ABI plan and is provisional, especially H,
where upstream and ROCKNIX disagree. Digital input stays `ANBERNIC-keys`;
axes are a second `adc-joystick` event device. Userspace needing one combined
stock event device must merge them; that ABI parity is not implemented here.

The 40XX/CubeXX stock trees enable UART5 on PH2/PH3 and describe PI7 RGB
control plus PWM3 on PI13. That does not establish MCU identity, baud,
commands or framing. No RGB LED class or invented serial protocol is added,
and UART5 is left disabled. PI13 is used only for the stock on/off motor;
PWM magnitude control is not implemented. Standard power/status LEDs use
PI12/PI11 with boot state retained.

USB-C port 0 stays peripheral, with PMIC VBUS sensing and the PI16 switch
available through the shared overlay. No ID pin role switching is enabled;
EHCI0/OHCI0 stay off. RG35XX H and Pro additionally expose the stock-described
separate USB1 host port (also described by upstream H) via PE5 -> PI7 supplies and EHCI1/OHCI1. Other vendor
trees copy generic usbc1 declarations; those alone do not prove a separate
physical connector. Additional host connectors on other models remain
unqualified/unimplemented rather than enabling generic copied controllers.

## RG34XX (`rg34xx`)

Stock FDT: offset 175261696, 137770 bytes; SHA-256 `e2f3ec2aa9ad78ce5ab553dc9f75fc04070fdb7b6cc405ba3f3af79b9fd9d3c0`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 720×480; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rg34xx-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 17 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC disabled; no sticks | No analog nodes | Not populated |
| Lid | No hall_key | No lid node | Not populated |
| Power / battery | AXP2202 R-I2C 0x34; 3500 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; PG18 reset, 200 ms delay; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PH2 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11 | gpio-leds power/status | Built; untested |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch | MUSB peripheral; host controllers off | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG34XX SP (`rg34xxsp`)

Stock FDT: offset 175486976, 137723 bytes; SHA-256 `1007ef137ade91b3c402f3b5d9fa1137e14bd452a9ffffd5e9e8e8332e9e54a6`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 720×480; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rg34xxsp-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 19 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC enabled; PI0/1/2 mux and PE8/9 clicks | 4 axes via ADC/mux; separate event device | Built; calibration/directions untested |
| Lid | hall_key PE7 active low | gpio-keys SW_LID; pull-up in GPIO flags; wake on opening | Built; wake untested |
| Power / battery | AXP2202 R-I2C 0x34; 3300 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; clock only; no reset GPIO in stock; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PH2 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11 | gpio-leds power/status | Built; untested |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch | MUSB peripheral; host controllers off | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG35XX Plus / RG35XX 2024 (`rg35xxplus`)

Stock FDT: offset 175867904, 137778 bytes; SHA-256 `cc984197630c72a19568e89c533c76406416f16a2e4627ab19ca38c91e8deb4d`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 640×480; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rg35xxplus-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 17 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC disabled; no sticks | No analog nodes | Not populated |
| Lid | No hall_key | No lid node | Not populated |
| Power / battery | AXP2202 R-I2C 0x34; 3000 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; PG18 reset, 200 ms delay; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PH2 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11 | gpio-leds power/status | Built; untested |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch | MUSB peripheral; host controllers off | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG35XX H (`rg35xxh`)

Stock FDT: offset 175394816, 138165 bytes; SHA-256 `8a808a205a0d30589d09d68ea0d9c0217948db4aabc15f549b7c2c0bbb082545`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 640×480; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rg35xxh-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 19 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC enabled; PI0/1/2 mux and PE8/9 clicks | 4 axes via ADC/mux; separate event device | Built; calibration/directions untested |
| Lid | No hall_key | No lid node | Not populated |
| Power / battery | AXP2202 R-I2C 0x34; 3000 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; PG18 reset, 200 ms delay; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PH2 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11 | gpio-leds power/status | Built; untested |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch; upstream H PE5 -> PI7 USB1 power chain | MUSB peripheral; EHCI1/OHCI1 separate host | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG35XX Pro (`rg35xxpro`)

Stock FDT: offset 175394816, 138165 bytes; SHA-256 `8a808a205a0d30589d09d68ea0d9c0217948db4aabc15f549b7c2c0bbb082545`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 640×480; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rg35xxpro-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 19 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC enabled; PI0/1/2 mux and PE8/9 clicks | 4 axes via ADC/mux; separate event device | Built; calibration/directions untested |
| Lid | No hall_key | No lid node | Not populated |
| Power / battery | AXP2202 R-I2C 0x34; 3000 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; PG18 reset, 200 ms delay; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PH2 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11 | gpio-leds power/status | Built; untested |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch; stock PE5 -> PI7 USB1 power chain | MUSB peripheral; EHCI1/OHCI1 separate host | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG35XX SP (`rg35xxsp`)

Stock FDT: offset 175327232, 137572 bytes; SHA-256 `5d64eefc6eca6554d8b0c956212ed9f1a8c5a693111c7238963dde1ebdca3888`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 640×480; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rg35xxsp-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 17 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC disabled; no sticks | No analog nodes | Not populated |
| Lid | hall_key PE7 active low | gpio-keys SW_LID; pull-up in GPIO flags; wake on opening | Built; wake untested |
| Power / battery | AXP2202 R-I2C 0x34; 3000 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; clock only; no reset GPIO in stock; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PH2 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11 | gpio-leds power/status | Built; untested |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch | MUSB peripheral; host controllers off | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG40XX H (`rg40xxh`)

Stock FDT: offset 175486976, 138099 bytes; SHA-256 `4af0dc7662fa0c44b5f4e7ef92d376499615f8622f9d9f488d5572fc4ff50a06`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 640×480; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rg40xxh-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 19 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC enabled; PI0/1/2 mux and PE8/9 clicks | 4 axes via ADC/mux; separate event device | Built; calibration/directions untested |
| Lid | No hall_key | No lid node | Not populated |
| Power / battery | AXP2202 R-I2C 0x34; 3200 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; PG18 reset, 200 ms delay; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PI13 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11; UART5 PH2/3, PI7, PWM3 | gpio-leds power/status; RGB protocol absent | Built; untested; RGB unsupported |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch | MUSB peripheral; host controllers off | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG40XX V (`rg40xxv`)

Stock FDT: offset 175486976, 138099 bytes; SHA-256 `4af0dc7662fa0c44b5f4e7ef92d376499615f8622f9d9f488d5572fc4ff50a06`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 640×480; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rg40xxv-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 18 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC enabled; PI0/1/2 mux and PE8/9 clicks | 2 axes via ADC/mux; separate event device | Built; calibration/directions untested |
| Lid | No hall_key | No lid node | Not populated |
| Power / battery | AXP2202 R-I2C 0x34; 3200 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; PG18 reset, 200 ms delay; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PI13 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11; UART5 PH2/3, PI7, PWM3 | gpio-leds power/status; RGB protocol absent | Built; untested; RGB unsupported |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch | MUSB peripheral; host controllers off | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG CubeXX (`rgcubexx`)

Stock FDT: offset 175394816, 138099 bytes; SHA-256 `401a8b0ce35a0a4eea220e1d0bbcb236e800e77b372c2ca396d97d3bd262311a`.

| Feature | Stock evidence | DT / driver | Status |
| --- | --- | --- | --- |
| Panel / backlight | lcd0; 720×720; PI8/9/10 SPI, PI14 reset, PI15 power; PWM0 | baseos,rgcubexx-panel; existing tables; pwm-backlight 20 us | Built; revision/adoption untested |
| Pad / volume | gpio_keys ANBERNIC-keys; 19 populated tuples | Inherited polled pad plus stock click codes where present | Exact pin/code/type/value comparison passed; untested |
| Sticks | GPADC enabled; PI0/1/2 mux and PE8/9 clicks | 4 axes via ADC/mux; separate event device | Built; calibration/directions untested |
| Lid | No hall_key | No lid node | Not populated |
| Power / battery | AXP2202 R-I2C 0x34; 3200 mAh; charge voltage 4100 mV | AXP717 BaseOS drivers; per-model overlay; axp2202-* ABI names | Built; charging and readings untested |
| Audio / headphone | codec PI5 amp, PI3 headphone detect; CLDO1 amp rail | Shared codec overlay, LINEOUT Speaker/Headphone routing; CLDO1 3.3 V | Built; jack/mute/levels untested |
| Wi-Fi / Bluetooth | SDIO1, stock 8821cs.ko and rtlbt/rtl8821c files; PG18 reset, 200 ms delay; UART1 PG19/17/16 | rtw88_8821cs; H5 hci_uart/btrtl; CLDO4 3.3 V and ALDO4 1.8 V | Built; firmware required; untested |
| Rumble | battery moto_pwm_gpio PI13 | gpio-vibrator, FF_RUMBLE on/off | Built; untested; variable PWM unsupported |
| LEDs / RGB | work_led PI12, low_pwr_led PI11; UART5 PH2/3, PI7, PWM3 | gpio-leds power/status; RGB protocol absent | Built; untested; RGB unsupported |
| TF1 / TF2 | MMC0 PF0–5; MMC2 PC5/6/8/10/13/15; PE22 detect, PE4 power, ALDO1 I/O | sunxi-mmc, fixed mmc aliases, TF2 50 MHz 3.3 V | Built; insertion/removal untested |
| USB | usbc0 PMIC VBUS, PI16 switch | MUSB peripheral; host controllers off | Built; untested; automatic USB-C role policy absent |
| RTC | R-I2C rtc@51 nxp,pcf8563 | PCF8563 rtc0, SoC rtc1 | Built; untested; external alarm IRQ unknown |
| HDMI | Enabled vendor HDMI, supplies and audio codec | No H616/H700 HDMI/PHY match/graph | Unsupported; report only |

## RG35XX 2024 identity

`devices.json` groups 2024 under `rg35xxplus`; there is no separate 2024 id,
stock image or DTB output. `rg35xxplus.dtb` describes the Plus radio hardware.
The upstream 2024 DTS omits radios, so do not treat this combined id as proof
of radio population on a 2024 unit. A distinct 2024 target needs its own id
and stock evidence; no speculative devices.json change was made.

## Modules for the startup script

Install the matching exported module tree with the Image. Names below are
modprobe names; dependencies are in modules.dep. Nothing was added to any
startup script or firmware directory.

| Model id | Modules to load |
| --- | --- |
| rg34xx | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart` |
| rg34xxsp | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart`, `sun20i_gpadc_iio`, `mux_gpio`, `iio_mux`, `adc_joystick` |
| rg35xxplus | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart` |
| rg35xxh | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart`, `sun20i_gpadc_iio`, `mux_gpio`, `iio_mux`, `adc_joystick`, `ehci_platform`, `ohci_platform` |
| rg35xxpro | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart`, `sun20i_gpadc_iio`, `mux_gpio`, `iio_mux`, `adc_joystick`, `ehci_platform`, `ohci_platform` |
| rg35xxsp | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart` |
| rg40xxh | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart`, `sun20i_gpadc_iio`, `mux_gpio`, `iio_mux`, `adc_joystick` |
| rg40xxv | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart`, `sun20i_gpadc_iio`, `mux_gpio`, `iio_mux`, `adc_joystick` |
| rgcubexx | `gpio_vibra`, `rtc_pcf8563`, `rtw88_8821cs`, `hci_uart`, `sun20i_gpadc_iio`, `mux_gpio`, `iio_mux`, `adc_joystick` |

Radio firmware paths are `rtw88/rtw8821c_fw.bin`,
`rtl_bt/rtl8821cs_fw.bin`, `rtl_bt/rtl8821cs_config.bin`, plus the matched
`regulatory.db`/`regulatory.db.p7s`. See RGSP.md for sources and stock fallback
cautions. Bluetooth/radio services and input device merging are userspace work.

## Validation

`sh v2/kernel/build.sh` builds Image, modules and all 11 model DTBs, exports
config and the committed kernel patch series. `docker exec v2-kbuild python3
/repo/v2/kernel/verify-models.py /k/models-stock` decodes every output, applies
its overlay with fdtoverlay, decodes the merged tree, verifies PMIC/panel/RTC
and USB device mode, and compares stock GPIO input tuples. The RG40XX V
comparison deliberately excludes the copied, unpopulated PE9 right click.
The RG28XX/RG SP output hashes match the pre-task artifacts exactly.
Optional stock DTBs live in the build volume only; no device/SD staging exists.

The exported config adds JOYSTICK_ADC, SUN20I_GPADC, IIO_MUX and MUX_GPIO
modules plus MULTIPLEXER. All four modules build. HDMI and RGB are unresolved
feature gaps; this document does not mark hardware parity achieved.

Kernel commit: `967ad23393af0c19897bf090d0df92adc85e2694` (Prashant Vaibhav <prashant@vaibhav.de>).
Kernel release: `7.3.1-baseos2+`. The kernel worktree is clean.
No BaseOS repository commit or push was made.
