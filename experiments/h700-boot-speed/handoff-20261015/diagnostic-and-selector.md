> Agent snapshot at STOP. [PROJECT-HANDOFF.md](../PROJECT-HANDOFF.md) is authoritative for the final restored device state; no diagnostic remains installed.

# Native IRQ/timer agent handoff — stopped at user request

Implementation is stopped. No further tests or device operations were performed for this handoff. This agent has never operated either lab device. Parent `/root` owns the in-flight `stage-phase-diagnostic` process/session 53088 and all live recovery decisions. Parent explicitly WILL NOT reboot the private diagnostic. Do not infer current card contents or pending RTC state from the host artifacts below: staging may have partially or fully completed. Parent must inspect its staging report and either restore the qualified v2 set or leave the lab in verified stock recovery before ending.

## Private RG40 helper phase diagnostic

Sources: `work/h700-loader/native-logo-v1/diag-src/{native,spl}` are private copies of the frozen canonical directories. `diag-src/canonical-before-pins.json` records the canonical snapshot. Sibling fastboot/variant-survey/evidence/FDT/trace paths are read-only links to original sources. The copied native, SPL and capsule builders have ROOT explicitly fixed to `/Users/pvaibhav/Code/Me/BaseOS`; their HERE/SPL paths use the private copies. Private `native/build.py` adds the purpose policy before generation hashing; private `native/early-display.c` alone changes firmware behavior, adding owned RTC phase writes. Private `native/verify-early-display.py` is a narrow work-only adaptation of the frozen real helper emulator. Canonical native/SPL sources were not edited for this diagnostic.

Build wrapper: `diag-artifacts-v1/build.py`; log: `diag-artifacts-v1/build.log`. Build completed exit 0. Outputs:

- Native: `diag-artifacts-v1/rg40xxv/native`.
- SPL: `diag-artifacts-v1/mainline/rg40xxv`.
- Capsule: `diag-artifacts-v1/rg40xxv/capsule`.
- Generation: `13d1a81b79d171ee2a3f21004278ba35439ad19ec8aaba22758c4d46e7b4095c`.
- Cookie: `0x05586a3c`; pending: `0x42535031`.
- Capsule SHA256: `fb27ab8b1d779c8180e893f394af29a79cacf45f3478ab37aa07233ae38178f1` (131072 bytes).
- SPL SHA256: `f9a34dff252d2c693be43c6faa9f7a5856b01dc1a13ca7482e3e5fdc8787c136`.
- Helper SHA256: `86ac42eb2abd8580aba769382d01a827d1e7fce6eecaf9e532ba01a8715f63c5`.
- Firmware SHA256: `c904536d53f52fca5a70350f331bd0136288200c5bd4443a697017d619d336fd` (286720 bytes).

Capsule manifest is explicitly `diagnostic=true`, `helper_phase_diagnostic=true`, and `inner_adoption_implemented=true` because this diagnostic still uses the strict adopting inner. Native `native_diagnostic=false` retains its old meaning of no kernel/monitor probes; separate `helper_phase_diagnostic` describes the RTC helper instrumentation. No production eligibility is claimed. Original kernel, monitor and main image were proved byte-identical to artifacts-v3.

Proof runner: `diag-artifacts-v1/prove-phase-helper.py`. Report: `diag-artifacts-v1/rg40xxv/phase-helper-verification.json`, SHA256 `d71184a83028b9391389ac0faeb500d36172066a48b1e034973ecd9582cb3e41`. Five actual linked A64 cases passed: valid low/BOE; first poll timeout; stuck scanline; wrong cookie; wrong pending. It reuses explicit architectural/MMIO models, preserves x18/x19..29/SP, checks GP0..13 and GP15 unchanged, and verifies private firmware source pins plus canonical snapshot unchanged. It is not a new full production qualification. Parent owns outer/ROM proof status; this agent did not run them for the private diagnostic.

At helper entry, GP14 must equal FNV(new-generation32) and GP15 must equal pending. Every phase compares the previous owned pair, writes only GP14, then DSB/readback. Success restores the original cookie before returning to SPL adoption checks. Phase map:

| GP14 | Completed point |
|---|---|
| `c0de0001` | Owned helper entry; before BMP conversion/cache clean/prepare |
| `c0de0002` | Prepare operations |
| `c0de0003` | Panel power/reset/probe |
| `c0de0004` | SPI stream and 50ms delay |
| `c0de0005` | Publish operations |
| `c0de0006` | TCON enable; before 100ms/scanline wait |
| `c0de0007` | Scanline advances |
| `c0de0008` | Backlight operations |
| `c0de0009` | Ready record published; immediately before cookie restore |
| `05586a3c` | Original cookie restored after success |

First poll failure leaves phase1; scanline failure leaves phase6. Unowned entry makes zero RTC/display writes. A malformed BMP in this private diagnostic leaves phase1, so later strict adoption refuses rather than preserving production optional-BMP behavior. The diagnostic has NOT been booted by this agent; parent instructed no diagnostic reboot.

## Safe owned RTC cleanup boundary

Actual owned registers are GP14=`0x07000138`, GP15=`0x0700013c`; vendor GP0..13 and retention `0x070001f8` are not ours. Do not use the rejected older `0x1d8/1dc` addresses. Never blindly clear an unexpected pair or decode an unexecuted phase as observed data.

Parent should first establish the actual staged generation/card state from its own report and a healthy stock or qualified v2 environment. Cleanup is permitted only after an exact current read, with the watchdog stopped under the existing owned-watchdog protocol and no trial executing. Use the already pinned compare-and-swap pair helper with the exact observed cookie/phase and pending; preserve and compare all GP0..13 before/after. If the pair is already zero, do nothing. For this diagnostic the initial expected pair is `05586a3c/42535031`; an observed `c0de0001..09/42535031` would need positive diagnostic provenance before cleanup. Existing helper CLI takes hexadecimal expected/current values: `rtc-trace --restore-pair EXPECTED_GP14 42535031 0 0`. Do not substitute a guessed expected value. Parent owns all device-side operations and restoration/readbacks.

## Frozen production logo host evidence

`artifacts-v3` completed all three host qualifications and was sealed once: 102 source pins, 440 artifact pins. Artifact-pins SHA256 `7f5c7543a6b13ec90b952040c20418449f8ac2aa2ffadc11d6ef8ffcead0353c`. All 440 pins matched on final reread before diagnostic work. Reports explicitly remain hardware-unqualified/non-install-eligible.

Actual SPL PIO -> actual linked A64 helper -> main-image six-case matrices passed all three targets. Frozen work driver `artifacts-v3/prove-logo-spl.py` SHA256 `c70a58e3009eff9194338f545e20f0086d63a85c271962640d9f54b003c953f9`. It snapshots its own source at startup and refuses a report if changed midrun. The previous one-extent-only oracle error and all older outputs/source are preserved at `artifacts-v3/coalescing-oracle-archive-v1`. RG40 actual live artwork is fragmented into precisely two maximal reads; the corrected independent FAT oracle checks both exact extents. `artifacts-v3/logo-matrix-source-audit.json` records all current report and binary pins.

Noack fixture bindings were created for BOTH lab targets using current live artwork and exact actual helper, producer, warm consumer and ROM proofs. `noack-fixture/verify.py --require-bound` and `test-prerequisites.py` passed two models. `noack-fixture/bound-prerequisite-report.json` SHA256 `bcd677d8c03f8fce20d08e78ab8d15ed37787f22dff5b047591f8932edd200e8`. These are host prerequisites, not hardware acceptance. Subsequent parent hardware findings supersede any assumption of successful early display: artwork-hidden RG40 boot passed, while artwork-present early helper failed before Linux health; the phase diagnostic was prepared to locate it. The user now requests exact full vendor-path reuse; initcall agent owns that investigation.

Canonical helper verifier owned by this agent: `experiments/h700-boot-speed/native/verify-early-display.py` frozen SHA256 `30880f27de4cea642eceb8275763c9377c24658054afe7a691b19b365174953f`. Canonical SPI verifier: `native/verify-panel-spi9.py` frozen SHA256 `cfae0838c37872bfd4fed2d42fc95abe807fd3ae102b67c781ddef0c1ae8bffd`. Header `native/boot-logo-file.h` is frozen SHA256 `436699784639996d24622943e127f57ebfae448f340ffbb5b07d6354453aae16`. These were not changed for diagnosis.

## Unfinished Step 2 slot selector — NOT tested or qualified

Only two new selector files were written before the urgent logo pivot:

- `experiments/h700-boot-speed/sequential/slot-reader.h`.
- `experiments/h700-boot-speed/sequential/test-slot-reader.py`.

Neither was compiled or executed. No output/test report exists. Treat both as a draft, review before continuing, and do not wire them into native/SPL. The test script is intended to compile actual C, run callbacks with sparse host card models, execute actual `tools/gptslot.c` flip on temporary regular sparse files, and compile an A64 object. No device path is used. Potential defects remain because no test ran.

Shared descriptor owner is `/root/initcall_probe`: `sequential/boot-blob.h`, packer and `tools/boot_payload.py`. Coordinate their final state separately. Agreed little-endian descriptor layout:

- 512 bytes at selected GPT entry5 start +472MiB; image starts immediately after header and is one contiguous 2048-aligned Android image. Header+image <=40MiB inside a fixed512MiB root slot. p4 recovery and p6 data are unchanged.
- Magic `BOSSEQ1\0`; u32 version@8=1, headerbytes@12=512, modelID@16, imagebytes@20, imageFNV@24, initrdbytes/FNV@28/32, librarybytes/FNV@36/40, headerFNV@44 (zero44..47 while hashing).
- u64 slotbytes@48=512MiB, tailoffset@56=472MiB.
- native ABI ID32@64, mutable root-build ID32@96; image/kernel/initrd/library SHA256 at128/160/192/224; reserved256..511 all zero.
- ABI ID is stable and distinct from per-card RTC attempt generation/cookie. Initcall agent derives ABI from model/source kernel/base DTB/protocol semantics, excluding serial/boot0/attempt generation/mutable libraries. Do not use the initial abandoned proposal of nm.generation as the stable ABI.
- ModelIDs follow sorted canonical target catalog: rg28xx1, rg34xx2, rg34xxsp3, rg35xxh4, rg35xxplus5, rg35xxpro6, rg35xxsp7, rg40xxh8, rg40xxv9, rgcubexx10, rgsp11.
- Root-build ID must be nonzero and is returned by firmware. `/init` must compare its raw32 bytes with selected ext4 `/etc/baseos/boot-payload-id` before switch_root. It must NOT be compiled into stable installed firmware. The draft reader has an optional expected-ID pointer solely for known policy/host mismatch cases.

Draft API: `baseos_seq_load(io, policy, destination, capacity, out)`; readonly callback sectors512, returns1 only complete. Policy carries model/native_abi/original kernelSHA plus optional expected rootID. Maximum four callback reads: GPT header, fixed8x128 entry table (two sectors), selected tail header, single payload. It validates primary header/table CRC and physical bounds, all partitions/nonoverlap, p4 boot / p5 rootfs / p6 UDISK names, 512MiB two-slot geometry, descriptor/ABI/kernel/root identity and whole image FNV. Output stays zero on failure. No GPT backup/inactive-slot retry or writes are implemented.

`tools/gptslot.c` is authoritative: base=p4.end+1; selected p5 may start at base or base+slot_sectors; p6 must start base+2*slot_sectors; existing whole-slot update+GPT flip remains the sole commit. Actual prepared vendor GPTs have appfs and are expected to refuse. The two live BaseOS inventory prefix GPTs are valid B and supply positive A/B fixtures. Native/SPL integration, initramfs identity, stable ABI policy integration, actual packed-payload cross-checks and every hardware qualification remain unfinished.
