# Mainline first-stage bring-up

The per-card native chain now passes boot, automatic stock fallback, production
health acknowledgment, display writeback and persistent suspend/resume on both
lab devices. See [the active checkpoint](../HANDOFF.md) for current release
status and remaining fleet coverage. The bring-up notes below preserve earlier
experiments and must not override that checkpoint.

Sources: official U-Boot v2026.10, archive SHA256
`f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739`.
The generic H700 LPDDR4 defconfig and ROCKNIX use 672 MHz, matching this card.
The probe uses the recovered board's drive, ODT and trained timing parameters.
The unmodified upstream SPL builds to 40 KiB; upstream SPL_MAX_SIZE is 0xbfa0
after its 0x60-byte image header. That is a code budget, not the size of SRAM A1.
[TF-A's hardware map](https://github.com/ARM-software/arm-trusted-firmware/blob/master/plat/allwinner/sun50i_h616/include/sunxi_mmap.h)
defines 32 KiB A1 at 0x20000 and borrowed VE/DE SRAM C at 0x28000. Keep the
entire warm probe and stack within A1; peripheral ownership of C may change.

The vendor monitor loads at **0x48000000**, from its image header +0x2c. Its
default next stage is AArch32 SVC at 0x4a000000 (SPSR 0x1d3), not the upstream
ATF AArch64 BL33 contract. Boot0 copies 32 DRAM words to monitor +0x30 and an
eight-byte board block to +0x24. No separate SCP item exists in this TOC1.
Preserving the vendor monitor therefore requires a compatible entry adapter.

`build-probe.py` compiles the upstream DRAM C as AArch32 behind a small MMIO
shim. Only include directives change. `build-trial.py` puts an SRAM-copy
adapter behind the verified vendor gzip/one-shot fallback; the primary gzip
and boot0 remain intact. `verify-probe.py` executes the real SRAM/PIO wrapper
with modeled SD registers; its DRAM return is modeled and is not training proof.
`run-trial.py` writes only the package, staged Android image, designated result
sector (p4+24 MiB), and the checked one-shot environment. The normal environment
is persisted before risky work. The watchdog returns to the normal kernel.

Controls so far reached normal fallback with all device health checks passing:

- v1: no durable checkpoint. The emulator accepted a stack in borrowed SRAM C.
- v2: pre-entry checkpoint 10, exact SRAM copy. Stack was 0x2f000 in SRAM C.
- v3: smaller stack 0x2b800 and emulator boundary; still checkpoint 10.
  The narrower emulator now rejects the actual v2 binary with an unmapped write.

The shared unproved premise is that the vendor MMU permits executing at SRAM
0x20000. Before another implementation change, `probe-copy.c` records the
translation registers and SRAM section descriptor in the durable journal.
The census found descriptor 0x00000c12 at TTBR0 0xbfff0059: strongly ordered,
execute-never SRAM mapping. v4 changes that section to normal executable
memory before branching and uses stack 0x27800 entirely in A1. The emulator
now checks both the real A1 limit and the section's memory type/permission.
No DRAM
initialization trial may run until SRAM execution and SD reporting are proved.

v4 passed on hardware: both result checkpoints and readback completed from
SRAM, followed by the requested watchdog reset and healthy original-kernel
fallback (boot ID bbca8947-6351-4774-a0dd-60b61a253573). This establishes the
SRAM/SD control only. DRAM training and cold BROM entry remain separate gates.

The fixed-parameter v4 DRAM trial also passed a warm retraining test. It
detected 2147483648 bytes, checked 16 KiB, and returned to normal gzip through
the watchdog. That result covers the lab RG34XX SP's LPDDR4 only.

The native AArch64 SPL SD control passed next. It initialized the card at
50 MHz with a four-bit bus before DRAM, copied a pinned 64 KiB boot0 into a
scratch extent, and verified every sector. Hardware ran at EL1 behind the
vendor monitor. The verifier runs the actual SPL startup and SD code without
mapping DRAM. `early-mmc.c` writes the scratch mirror at sector 0x56200.
It does not restore the primary boot0. Cold BROM entry remains untested.

## Automatic DRAM selection experiment

`build-probe.py --autodetect --boot0 FILE` builds an SRAM candidate selector
from the primary and alternate parameter banks in a pinned boot0. It includes
the upstream DDR3, LPDDR3, and LPDDR4 timing functions. Each timing function
uses its candidate's clock, including RG28XX's 792 MHz DDR3 bank. The fixed
mainline entry is replaced with bounded candidate retries. Upstream training
timeouts and exhausted rank/width attempts restore an ARM register and stack
checkpoint, then try the next bank. The manifest lists these source changes.
The pinned upstream LPDDR3 TPR6 shift is already 16. No ROCKNIX timing patch
is needed on v2026.10.

Every model's boot0 also carries a generic LPDDR4 bank with tpr11 = tpr12 = 0.
Vendor boot0 searches those DQ delays; mainline programs them as given, so
training "passes", size detection sees no aliasing and `mctl_calc_size` probes
outside the DRAM window, which hangs until the watchdog starts stock (seen on
1 GiB LPDDR4 units, 1.5.1). `build-early-mmc.py` fills such banks with the
v2026.10 Anbernic H700 defaults 0x1b1f1e1c / 0x06060606; tuned banks are left
alone (`test-dram-candidates.py`). The selector also refuses a detected layout
whose size probe would leave the 32-bit window (more than 2 GiB) and, before
accepting a bank, checks that the first words and the words halfway up hold
their own values; either failure moves on to the next bank.

`verify-dram-select.py` executes the selector, the real rank/width retry loop,
the stack unwind, and the timing register writes. Training success and detected
geometry are modeled. `verify-variants.py` builds and checks all eleven prepared
model banks. All eleven passed, with 66 retry cases, 66 timing cases, and
44 result-protocol cases. Reproduce with:

    /tmp/uboot-venv/bin/python experiments/h700-boot-speed/spl/verify-variants.py work/h700-loader/mainline/u-boot-v2026.10.tar.gz work/h700-loader/mainline/variant-probes-FRESH

The automatic selector also passed a warm RG34XX SP trial. It selected type 8
on its first attempt, detected 2 GiB, passed the memory check, and returned to
healthy normal gzip. Probe SHA256 is
812cb33f2f717337a21437a182e8dd25508d0e8804424aa4737cde0e5623e13b.
The complete probe is 13660 bytes and fits SRAM A1. Hardware evidence is in
`../evidence/dram-selection.json`. Failed-type retries have emulation coverage
only. LPDDR3 training on the RG40XX V and cold startup remain untested.

`inventory.py` pins both devices' current boot0, TOC1, environment, full boot
partition, live FDT, and prefix through the first partition boundary. It refuses
to overwrite a snapshot. The lab serials are explicit. The fresh snapshots
confirm valid eGON images at sectors 16 and 512 on both cards. That observation
does not establish BROM fallback behavior at sector 512.

## Temporary RTC trace cleanup

`rtc-trace.c` reads all sixteen general-purpose words through a read-only
mapping by default. `--restore ORIGINAL15` retains the old marker-guarded GP15
cleanup. `--restore-pair EXPECTED14 EXPECTED15 ORIGINAL14 ORIGINAL15` restores
only GP14 and GP15, requiring the current words to match the captured trial
values before writing. Arguments are one to eight hexadecimal digits, with
optional `0x`. Supply the original values from the pre-trial RTC evidence.
For the current experiment both original words are `00000000`.

The helper rechecks the pair immediately before writing, checks GP15 again
before its write, and verifies both final values. Two MMIO writes are not
atomic: if a concurrent writer changes GP15 after GP14 is restored, cleanup
stops and reports the observed pair. Exit 2 means invalid arguments, 3 means
the expected current state changed, and 4 means readback failed. Never retry
with guessed expectations.

Build with the existing `baseos-h700-mainline` Docker toolchain, whose `/work`
mount is `work/h700-loader/mainline`:

```sh
cp experiments/h700-boot-speed/spl/rtc-trace.c work/h700-loader/mainline/rtc-trace.c
docker exec baseos-h700-mainline aarch64-linux-gnu-gcc -std=c11 -O2 -Wall -Wextra -Werror -static /work/rtc-trace.c -o /work/rtc-trace
```

Run the host-only parser and fake-register proof without `/dev/mem` access:

```sh
clang -std=c11 -O2 -Wall -Wextra -Werror experiments/h700-boot-speed/spl/verify-rtc-trace.c -o /tmp/h700-verify-rtc-trace
/tmp/h700-verify-rtc-trace
```
