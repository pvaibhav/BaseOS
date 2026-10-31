# RG28XX TF1 UHS research and proposal

Research date: 2026-10-31. Research only. No builds, kernel/config changes, handheld access, installs, commits, or pushes. Local inspection and this note were done through `v2-kbuild`; `/k/linux` was read only.

## Recommendation

Do not enable UHS on TF1 yet. The H616 controller hardware has UHS capability, and mainline has some of the necessary pieces, but the available RG28XX evidence does **not** establish an independently switchable TF1 signal supply. Stock explicitly disables voltage switching and caps TF1 at 50 MHz. A safe, board-specific UHS DT change cannot be completed by guessing an AXP717 output.

The strongest wiring clue is the upstream related RG35XX-2024 board: its TF1 card power and PF pin bank both use CLDO3, which also powers several other pin banks and is fixed at 3.3 V. If the RG28XX uses that same wiring, UHS cannot be enabled correctly with a software-only DT change. Lowering that shared rail would also lower card VDD and unrelated I/O supplies. RG28XX-specific schematic/continuity evidence is needed to settle this, rather than treating another model's DTS as proof.

## 1. What stock actually configures

Local evidence:

- `work/v2/hw/rg28xx-stock.dts`, lines 3664–3689: TF1 is `/soc@03000000/sdmmc@04020000`, `device_type = "sdc0"`, compatible `allwinner,sunxi-mmc-v4p1x`.
- It has `max-frequency = <0x2faf080>` (50,000,000), `bus-width = <4>`, `cap-sd-highspeed`, `non-removable`, `no-sdio`, `no-mmc`, `sunxi-dis-signal-vol-sw`, and `ctl-spec-caps = <0x608>`.
- There are **no** `sd-uhs-*`, `vmmc-supply`, `vqmmc-supply`, or power/voltage-select GPIO properties on TF1. No interpretation of the undocumented `ctl-spec-caps` bits is needed to establish the explicit frequency limit and absence of UHS declarations; those BSP bits must not be copied into mainline.
- The separately inventoried `work/mainline/hardware/rg28xx.vendor.dts` agrees. Its inventory provenance in `summary.json` records DTB SHA256 `343fd11c8f578ef2dd1d0f5aa0727172e70571a8f98b2ca4cfc61a45833824bb`.
- The sampled RG35XX Plus, RG35XX H, and RG40XX V vendor dumps have the same TF1 policy. This is evidence about these captured versions, not every Anbernic firmware release.

Do not confuse the other controllers with TF1:

- `sdmmc@04022000` (TF2) has `vqmmc-supply = <0x54>`. That phandle resolves to **ALDO1**, vendor name `axp2202-aldo1` (lines 2919–2926). It also has a PE4 power GPIO and disables signal switching. That reference does not establish any ALDO1 connection to TF1.
- `sdmmc@04021000` (SDIO controller) has SDR50/DDR50/SDR104 declarations, a 150 MHz limit, `sdio-used-1v8`, and `no-sd`. These are not TF1 capabilities. RG28XX lacks built-in Wi-Fi, so an enabled template node alone is not proof of an attached SDIO device either.
- TF1 uses PF0–PF5 (stock lines 1651–1660). PF6 card detect, where present on related boards, is not a voltage-select pin.

The public [RG28XX boot information capture](https://gist.github.com/macromorgan/6bf2fcdc8093b88765c48343ad63a995) shows BOOT0 selecting the high-speed/SDR25 class at 50 MHz. That boot-loader label does not establish 1.8 V UHS SDR25; the same label covers ordinary SD high-speed. Its regulator summary attaches `sdc2` to ALDO1, consistent with the TF2 reference. It does not provide TF1 electrical wiring or a kernel TF1 UHS result.

There is a potentially misleading [RG35XX Plus experiment](https://gist.github.com/acmeplus/24290b784c727c015c5b44bc094baf0d?permalink_comment_id=4981281): a vendor Linux 5.4.125/Orange Pi Zero2w setup reports TF1 SDR104 at 150 MHz. The same log says it found no vmmc, vqmmc, or voltage-switch regulators. This is a different board/software experiment, without measured pin voltage or transfer-integrity evidence. It proves a BSP can report SDR104 timing, not that stock RG28XX switches to 1.8 V correctly.

**Answer about the voltage switch:** no TF1 regulator or GPIO that switches 3.3/1.8 V has been identified in the inspected RG28XX evidence. ALDO1 is not a justified answer. CLDO3 is a shared-rail clue, not a safe switch candidate.

## 2. Mainline support and its limits

Inspected `/k/linux/Makefile` identifies 7.3.1. Relevant code:

- `sun50i-h616.dtsi:616`: `allwinner,sun50i-h616-mmc`, fallback `allwinner,sun50i-a100-mmc`. The SoC node has a 150 MHz maximum; the BaseOS board overrides it to 50 MHz.
- `drivers/mmc/host/sunxi-mmc.c:1193`: H616 configuration uses new timings, sample-delay calibration, DATA0 masking, and shifted DMA descriptors. It is not excluded by the higher-mode capability mask at lines 1444–1454.
- `sunxi_mmc_volt_switch()` around line 953 calls `mmc_regulator_set_vqmmc()`. Without a real vqmmc it only accepts 3.3 V. Thus the voltage callback exists, but cannot create missing hardware.
- `mmc_of_parse()` in `drivers/mmc/core/host.c` reads `sd-uhs-sdr50`, `sd-uhs-sdr104`, `sd-uhs-ddr50`, and `max-frequency`. The driver's initial 52 MHz `f_max` is overwritten by this DT property, not an absolute 52 MHz driver ceiling.
- New timings bypass the old clock-phase table. `sunxi_mmc_calibrate()` around line 702 writes software sample delay **zero**; its FIXME says the calibration process is not understood. This is not a per-card tuning sweep.
- `sunxi_mmc_ops` around line 1110 has **no `execute_tuning`**. `drivers/mmc/core/core.c:931` returns success when this callback is absent. A card can therefore be labelled SDR104 without actually passing CMD19 tuning.
- DDR50 sets the controller DDR bit, but `sunxi_mmc_clk_set_rate()` around line 786 doubles the module clock only for `MMC_TIMING_MMC_DDR52`. The comment explicitly says other DDR modes need work. DDR50 cannot be assumed correct just because its cap parses.
- `SDXC_VOLTAGE_SWITCH` is defined as bit 28 but unused; request construction has no CMD11-specific programming. A real implementation needs the H616 CMD11/clock-stop/completion sequence checked against the hardware manual/BSP. This is an audit requirement, not a claim that setting one bit alone fixes it.
- `drivers/pinctrl/sunxi/pinctrl-sun50i-h616.c:907` selects `BIAS_VOLTAGE_PIO_POW_MODE_CTL`. `pinctrl-sunxi.c:725` programs the bank voltage mode, but calls it when requesting the bank regulator around line 941. There is no voltage-change notifier in the inspected path. A 3.3→1.8 V change also needs the PF pad mode updated at the correct point, and restored on failure/resume/reboot.
- `no-1-8-v` is not read by this sunxi driver or its generic `mmc_of_parse()` path. The present fixed 3.3 V supply and absent UHS caps are the effective barriers. Do not depend on that boolean alone to prevent UHS here.

So: SDR50/SDR104 have a clock/capability path and voltage callback, but full removable-SD UHS operation is not demonstrated on this board. SDR50 is the first useful single-data-rate experiment once wiring and pad switching are resolved. SDR104 needs real tuning for a reliable product; DDR50 needs its clock setup corrected too.

Upstream context:

- The [2016 vqmmc/eMMC series](https://www.spinics.net/lists/arm-kernel/msg475819.html) explicitly distinguished hardware UHS capability from untested voltage switching on boards lacking dedicated supplies.
- The [follow-up series](https://lkml.indiana.edu/hypermail/linux/kernel/1601.3/06261.html) dropped a proposed CMD11 block; the author expected UHS caps to remain unset. Do not look for a current explicit CMD11 rejection in the driver.
- The [2017 A64 series](https://lkml.iu.edu/hypermail/linux/kernel/1701.3/01777.html) improved clock gating, zero delay and DATA0 masking toward SDR104. These mechanisms exist in the inspected H616 path. It is not H700 board qualification or proof of automatic tuning.
- The [2019 H6 DDR discussion](https://groups.google.com/g/linux-sunxi/c/8Z20PtRP-gY) reports real failures and discusses missing dynamic pin-bank voltage configuration. It concerns H6 eMMC, not a proven RG28XX bug, but explains why a supply-only change is insufficient.
- The [upstream related H700 DTS](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rg35xx-2024.dts) assigns TF1 VDD and VCC-PF to shared CLDO3, fixed at 3.3 V.

No ready-to-apply H616/RG28XX UHS tuning patch or board-specific UHS qualification was found in this search. This is a search result, not proof that no private or downstream patch exists.

## 3. Concrete change proposal

### Current evidence: keep the existing 50 MHz configuration

Do not change the BaseOS fixed supply, enable UHS properties, widen CLDO3 to 1.8–3.3 V, or assign TF1 to ALDO1. No driver patch can make shared card VDD and I/O power into separate supplies.

The AXP717 binding is in `Documentation/devicetree/bindings/mfd/x-powers,axp152.yaml`, not a standalone AXP717 regulator file. `drivers/regulator/axp20x-regulator.c:789–846` exposes dcdc1–4, aldo1–4, bldo1–4, cldo1–4, cpusldo and boost. ALDO/BLDO/CLDO outputs support 0.5–3.5 V in 0.1 V steps. That confirms regulator capability, not board connectivity. Existing DCDC1, ALDO4 and CLDO1 consumers must stay untouched.

### Only if an independent TF1 signal rail is established

The following is the exact **property shape**, not a flashable RG28XX wiring claim. `tf1_vdd` and `tf1_io` must be labels on proven physical regulators; there is deliberately no invented AXP output number.

1. In the base board DTS source, remove `no-1-8-v`, and remove both current mmc0 supply assignments to `reg_sd_boot`. Keep the fixed node because display/pin-bank consumers use it. `/delete-property/` in a separately applied DT overlay does not delete existing live-tree properties; do these removals in the base DTS.
2. Add actual rail nodes under the PMIC regulators in `v2/kernel/src/baseos-rg28xx.dtso`. Card VDD stays at 3.3 V; signal rail constraints allow both values. Do not use `regulator-fixed` for the signal rail. A proven GPIO selector would instead need a `regulator-gpio` description with its measured GPIO, polarity and voltage states.
3. Add these consumer properties to that early overlay (it is applied by `drivers/of/baseos-overlay.c` before devices are created):

```dts
&mmc0 {
    vmmc-supply = <&tf1_vdd>;
    vqmmc-supply = <&tf1_io>;
    bus-width = <4>;
    cap-sd-highspeed;
    sd-uhs-sdr50;
    max-frequency = <100000000>;
};

&pio {
    vcc-pf-supply = <&tf1_io>;
};
```

The actual `tf1_io` regulator needs `regulator-min-microvolt = <1800000>; regulator-max-microvolt = <3300000>; regulator-boot-on;`. It must start at 3.3 V for card initialization, then change only through the MMC voltage-switch sequence. Those min/max values do not select the startup voltage. No pinctrl consumer may hold a conflicting fixed-3.3 V voltage request. TF1 VDD must remain 3.3 V and needs independent power control if reliable card power cycling is required; an always-on dummy supply does not provide that.

4. Implement/review dynamic PF voltage-mode switching and the CMD11 sequence first. SDR50 may use the existing zero sample-delay path for a controlled trial, but needs integrity validation; add tuning if the controller/board requires it.
5. For SDR104, replace the trial mode declaration with `sd-uhs-sdr104;` (or retain SDR50 as an additional advertised mode) and use `max-frequency = <150000000>;`, matching the H616 SoC description. SDR104 does not require a 208 MHz clock, and raising this board directly to 208 MHz is not justified. Add a real `execute_tuning` callback: sweep valid H616 sample taps using `mmc_send_tuning()`, choose the middle of a valid window, fail when no window exists, and preserve/restore/reapply the chosen value across clock changes and resume. The current calibrate-to-zero call must not overwrite the result. Tap ranges, clock phase and wrap behavior need the H616 manual/BSP checked; this note does not invent register values.
6. For a separate DDR50 trial, advertise only `sd-uhs-ddr50; max-frequency = <50000000>;`. Extend the module-clock doubling condition to include `MMC_TIMING_UHS_DDR50` for new-timing hosts, then validate divider and actual card clock. This small clock fix alone does not solve voltage switching or establish signal integrity.

### Restart, card and stock-OS risks

A card that fails CMD11 or tuning may need a genuine card-VDD power cycle before high-speed fallback works. Powering down an always-on logical supply will not reset it. Keep normal 3.3 V high-speed operation available for cards that cannot use UHS.

Normal MMC shutdown calls card power-off, but regulator disable is not a guarantee of 3.3 V restoration or physical power loss. Warm/watchdog resets can leave a card in 1.8 V mode or a PMIC/pad bank in the previous state. BOOT0 needs a known starting state before Linux runs. A Linux shutdown hook cannot cover watchdog resets. Resolve this in the earliest boot stage/card-power design, with proper reset/power-cycle ordering; simply raising host I/O to 3.3 V while the card remains in 1.8 V mode is not a complete reset procedure.

UHS mode is not stored as a persistent filesystem setting on the card. A true power cycle normally returns ordinary initialization. The stock OS on the same card still needs its expected 3.3 V electrical state on handoff. Keep stock firmware DT policy intact and test both boot paths. Wrong timing can also corrupt real writes; read speed alone is insufficient for release qualification.

## 4. Device verification for the operator

No commands below were run on the handheld.

First identify the host/block mapping rather than assuming mmc numbering: inspect `/sys/class/mmc_host/mmc0/device` and `/sys/block/mmcblk0/device` symlinks and confirm the host at `4020000`. With debugfs mounted, collect:

```sh
cat /sys/kernel/debug/mmc0/ios
cat /sys/kernel/debug/regulator/regulator_summary
dmesg | grep -iE 'mmc|voltage|tuning|crc|timeout'
```

A successful SDR50 run should show `bus width: 2 (4 bits)`, `timing spec: 5 (sd uhs SDR50)`, and `signal voltage: 1 (1.80 V)`, with requested/actual clock near 100 MHz. SDR104 is timing 6 and about 150 MHz under the proposed cap. DDR50 is timing 7 and 50 MHz; its double data rate distinguishes it from high-speed. Clock rounding can reduce the actual rate. The `vdd` line should still describe the card's roughly 3.3 V VDD range: it is different from the `signal voltage` line. A displayed 1.8 V is software state; verify physical PF supply voltage as well when establishing the wiring.

For a read-only, cache-bypassing throughput measurement, after confirming the block device:

```sh
dd if=/dev/mmcblk0 of=/dev/null bs=4M skip=64 count=256 iflag=direct
```

This reads 1 GiB beginning at 256 MiB; it writes no card files and changes no partition. Use a card large enough for the range. If the available dd lacks direct I/O, use an already available equivalent, or explicitly label a buffered result as such; do not silently compare it with direct I/O. Run three times with game/library scans idle, and compare the median using the same card, region, software and CPU policy. Inspect ios during I/O if runtime clock gating makes idle clocks ambiguous. Do not use a buffered read of a small cached game as evidence of bus speed.

Then test repeatable reads of existing large files against a trusted digest, several cold boots, warm reboots, suspend/resume, watchdog reset recovery, and return to stock OS. Record any CRC/timeouts/retries as failure even when dd completes. Do not write benchmark files, manifests, scripts or logs to the SD. Operator output can be captured elsewhere. Existing saves/settings must stay in place.

The reported ~22 MB/s baseline is user-provided and was not remeasured. This research makes no measured speedup claim.
