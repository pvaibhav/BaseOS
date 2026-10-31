# RG28XX GPU frequency scaling

Built with `v2/kernel/build.sh` in `v2-kbuild`. Kernel-tree commit:
`09b89845d` (no repository commit). New exported patch:
`0032-arm64-dts-allwinner-rg28xx-scale-GPU-on-the-retained.patch`.

## Rates and voltage evidence

The captured RG28XX stock tree, `work/v2/hw/rg28xx-stock.dts:6170`, has
these legacy OPP pairs (frequency in kHz, voltage in microvolts):

```
648000 960000
600000 900000
552000 900000
504000 900000
456000 900000
420000 900000
```

The enabled table is 432, 456, 504, 552 and 600 MHz. Every entry uses
`opp-microvolt = <900000 900000 940000>` (target, minimum, maximum).
This permits the existing fixed 940000 uV supply; it cannot change hardware
voltage. The four 456–600 MHz points come directly from stock. The 432 MHz
entry preserves the loader clock reported for this board. Its voltage floor
is inferred conservatively from the stock 456 MHz point, which needs 900 mV.
420 MHz is omitted to avoid an idle downshift during boot. 648 MHz is omitted
because its stock 960 mV requirement exceeds the retained 940 mV rail.

The physical supply is understood to be AXP717 DCDC2, shared with system
logic. Evidence: the existing `kernel/u4-gpu-README.md` hardware decision and
upstream `arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rg35xx-2024.dts`
in `/k/linux`: GPU `mali-supply` references DCDC2, named `vdd-gpu-sys`,
with minimum = maximum = 940000 uV. This is family-board evidence, not a new
measurement of this RG28XX. The captured stock GPU node supplies voltage
requirements but has no GPU regulator phandle, so it alone does not prove
board wiring. Do not lower a shared rail based only on GPU OPP requirements.

The base DTS retains `reg_gpu_sys_retained` and its existing `mali-supply`.
No DCDC2 control is added. Neither the repository overlay source nor
`drivers/of/baseos-rg28xx.dtso` changes. `build.sh` builds the already-present
kernel-tree overlay; it does not copy `v2/kernel/src` into the kernel tree.
The two overlay sources were compared and are identical.

## Boot behavior and the previous delay

Removed unused private `BASEOS_H616_PLL_GPU` definition. No assigned clock
rate or parent is added. Panfrost selects the OPP at or above the current
clock during probe and calls `dev_pm_opp_set_opp()`. With the expected loader
clock of 432 MHz, it selects exactly 432 MHz. The clock core returns early
for an unchanged rounded rate (`drivers/clk/clk.c:2587`). Idle governor polls
also select the 432 MHz floor. The supply remains fixed, so these operations
do not program the PMIC. Frequency changes happen when GPU load calls for them.
This avoids the known synchronous boot clock change; it is not a measured
claim of identical total boot time. A different loader clock needs rechecking.

The old assigned-clock change is recorded in kernel commit `466012ec4`.
`ccu_nkmp_set_rate()` waits for PLL lock through
`ccu_helper_wait_for_lock()`, whose timeout is 70000 us with 100 us polls.
The GPU PLL notifier can also wait for lock. The GPU mux notifier only delays
1 us per event. Thus a PLL-lock timeout is the source-based explanation that
fits the reported roughly 75 ms delay, rather than mux reparenting delay.
No historical warning stack or live trace was available to prove which lock
wait timed out or why the lock bit failed. The timeout is not shortened or
removed. Runtime rate changes may expose the same issue and need testing.

## Artifact checks

`CONFIG_DRM_PANFROST`, `CONFIG_PM_DEVFREQ`, `CONFIG_PM_OPP`, and
`CONFIG_DEVFREQ_GOV_SIMPLE_ONDEMAND` were already `y` and remain `y` in the
exported config. No config or driver change was needed.

The build exported `work/v2/kernel/Image` and `work/v2/kernel/rg28xx.dtb`.
The exported DTB was decoded with the built dtc. Its five 64-bit OPP rates,
voltage triplets, GPU table reference, fixed supply reference, and absence of
assigned GPU clocks were checked. The actual built-in PMIC overlay was applied
with fdtoverlay, and the merged DTB passed the same checks. Kernel git diff
checks passed and its working tree is clean. The build script ran inside the
target container using a temporary Docker-command wrapper that executed its
inner build locally; no build-script changes or host packages were needed.

## Tests to run on the handheld

1. Compare several cold boots with the previous kernel using the same boot
   timing method and workload. Check timestamped dmesg for the initial GPU
   clock of 432000000, Panfrost probe failures, deferred probes, and CCU/PLL
   timeout warnings. A boot without GPU clients should stay at 432 MHz.
2. Locate the GPU under `/sys/class/devfreq/` (normally `1800000.gpu`). Read
   `governor`, `available_frequencies`, `cur_freq`, and `trans_stat`.
   Expect `simple_ondemand` and the five rates above, with no 648 MHz entry.
3. Run a known Panfrost GPU workload. Sample `cur_freq`, then read `trans_stat`
   again. Expect 600000000 under sustained load and 432000000 after idle
   while the device is active. Watch dmesg for lock waits, GPU faults, resets,
   and job timeouts. Check the clock summary if available: devfreq statistics
   alone do not prove that the hardware reached the requested rate.
4. Run a longer load and check temperature, display stability, and suspend,
   resume and reboot behavior. The table does not add thermal cooling maps.
   Any thermal policy must be checked separately. Verify the actual DCDC2
   voltage and board wiring before considering a future 648 MHz change.

No handheld access, installations, backups, repository commits or pushes were
performed. Changes are limited to the kernel DTS patch, build exports, and
this note. The safe maximum supported by the retained-rail evidence is
600 MHz; 648 MHz remains intentionally unavailable.

## Update: 648 MHz added

Measured on the stock 1.5.3 OS (RG28XX): with NextUI pinning the GPU at
648 MHz, DCDC2 (`axp2202-dcdc2`) reads 940000 uV and has no users. The stock
Mali driver never applies the 960 mV from its table, so stock runs 648 MHz
at 0.94 V. Patch 0033 adds the same 648 MHz point at 0.94 V.
