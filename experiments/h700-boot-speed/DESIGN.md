# H700 boot-chain design and historical decisions

The selected release architecture is mainline SPL plus a RAM-only A32 loader,
with the original vendor kernel, monitor and SCP. Its per-card implementation passes native boot, display writeback, automatic
stock recovery and persistent suspend/resume checks on both lab devices.
Generic fleet DRAM and image qualification remain open. [HANDOFF.md](HANDOFF.md) contains the current checkpoint and
[PLAN.md](PLAN.md) lists the remaining acceptance gates.

The retained-vendor-path design below records the initial comparison. Its
conditional milestones and hardware-coverage statements describe that earlier
checkpoint. The later native-loader section records the selected architecture;
current hardware results take precedence over historical status statements.

## First implementation: retain the vendor hardware path

The observable contract is the same kernel, rootfs and frontend, at least one
second or 25% earlier at frontend exec, with normal device behavior preserved.
Build complete pinned artifacts, run their actual instructions in Unicorn,
then use `trial.py` for checked install, safe restart, evidence and restore.

Two independent sketches compared a standalone AArch32 loader with an
in-memory compressed vendor fallback, and a vendor fast path with an injected
LZ4 decoder and optional removal of the bootloader splash. Independent review
scored them 16/25 and 19/25 respectively on potential gain, compatibility,
recovery, implementation surface and measurement. The retained path is the
base: it changes one operation while preserving PMIC, SD, FDT, cache cleanup,
charger decisions and the monitor. It does not yet establish the target gain.
Revisit the standalone loader if surviving init stamps identify enough
removable vendor time to justify new SD, PMIC and handoff implementations.

The entry instrument is part of the bring-up, not a performance optimization.
Pre-logo stamps must live outside the bitmap at `0x40000000`. The fixed v4
block uses `0x4fff00f8`; existing real bootm and every stub passed emulation.
The original v3 scratch allocation invalidated its hardware observations.
Move exported stamps into an actual FDT property as instrumentation evolves.

The compression adapter has the existing gunzip ABI:

```c
int unpack_kernel(void *destination, unsigned long capacity,
                  const unsigned char *source, unsigned long *length);
```

One builder owns pin checks, hook addresses, reclaimed help text, generated
symbols, allowed changes, checksums and the matched Android image. A bounded
raw LZ4 block uses explicit framing and exact input/output sizes. Unknown
framing uses stock gunzip; malformed recognized framing must fail. Successful
decode must produce the original kernel bytes at `0x40080000`, leave the
same ramdisk/FDT/command line, and reach the same monitor call.

The first decoder candidate retains the splash to isolate compression. A
separate no-splash candidate must preserve PE6 panel selection, remove stale
boot-display advertisements, and prove the kernel initializes the physical
panel. From the standalone sketch, retain the requirement to check the first
visible picture as well as frontend exec; moving display work into Linux
cannot be counted as removed work.

The live boot0 primary is already trained LPDDR4, type 8. The original
prepared prefix's LPDDR3 primary is not the current on-card state. Reject the
proposed copy-alternate-to-primary experiment: it has no supported mechanism
for a gain. Preserve boot0 and its executable.

The user explicitly authorized lab firmware writes and restores. The first
LZ4 trial required manual card recovery because an unaligned word copy faulted
under SCTLR.A. Current builders reproduce that failure and reject it. New
trials consume a one-shot environment before risky work, retain the primary
gzip image, and use an independently tested watchdog fallback.

The user subsequently specified mainline SPL plus a stripped loader as step 1,
with a local commit and installation before sequential kernel/initramfs/library
loading in step 2. Retained boot0 is only a checkpoint: v5 measures 2.612 s
before U-Boot entry, already exceeding the total target. Kernel rebuilding
belongs to step 3 and has not been started.

Upstream U-Boot v2026.10 supports H700 LPDDR4; ROCKNIX uses the same basic
configuration. The live RG34XX SP header agrees on 672 MHz, type 8, drive and
ODT settings, but differs in TPR6, TPR11 and TPR12. Use live board evidence
rather than assuming the generic configuration is exact. Preserve the vendor
monitor and prove its entry contract; mainline ATF behavior alone does not
prove vendor-kernel compatibility.

Manual recovery is unavailable. A chainloaded SRAM DRAM test must leave the
known first stage intact and consume the trial before arming its watchdog.
Replacing the first stage additionally requires a recovery path that runs
before DRAM initialization; chainloading alone cannot establish cold boot.

The release must support all eleven models without user configuration. The
generic LPDDR4 defconfig covers only one memory type. The SRAM prototype now
tries vendor parameter banks and selects the corresponding mainline DDR3,
LPDDR3, or LPDDR4 timing function. Clock conversion uses the runtime candidate,
since RG28XX includes a 792 MHz DDR3 bank alongside 672 MHz LPDDR4. Failed
training unwinds to an SRAM checkpoint before the next controller reset.
All prepared model banks pass instruction tests, but hardware training so far
covers only the RG34XX SP LPDDR4 path. Native SPL integration and RG40XX V
LPDDR3 trials remain. See evidence/compatibility.json for the per-model panel
contract. RG28XX and CubeXX differ from the shared PE6 token rule.

## Selected native loader: SPL preloads, A32 BL33 consumes RAM

The native monitor census has now reached A32 BL33 on the RG34XX SP. The next
implementation replaces that vendor BL33 with a RAM-only loader. The image
builder exposes one build operation; mainline's board hook calls
`baseos_spl_boot()`; the monitor enters `baseos_loader_boot()` at `0x4a000000`.
Neither caller selects a DRAM type, panel revision, driver or address.

Three independent sketches compared a RAM-only A32 loader, a RAM-only A64
loader, and a cached MMC callback into retained SPL state. Parent scores on
gain, compatibility, recovery, ownership and verification were 22/25, 17/25
and 15/25. The independent judge scored 22/25, 16/25 and 14/25 and also selected
the A32 design. A64 adds a monitor transition without an address-width need;
the callback exposes SPL globals, allocator, x18 and controller lifetime across
BL31. Both are rejected. There is one MMC owner and no callback ABI.

The selected sequence is:

1. SPL arms the watchdog before SD or DRAM work and establishes SRAM-only
   recovery. Bring-up always restores and checks the original 64 KiB boot0
   before trying native DRAM or handoff.
2. Native mainline MMC, PMIC and automatic DRAM selection run in SRAM.
   Temporary EL3 identity mappings make SRAM executable Normal memory,
   peripherals Device/XN and the required DRAM Normal WBWA.
3. SPL preloads the monitor, its model DTB/DTBO, own BL33 and complete Android
   image, publishes an immutable data record, cleans to PoC, disables caches
   and MMU, and uses the proved vendor RMR entry contract.
4. Unmodified BL31 returns to A32 SVC BL33. Own assembly establishes stack,
   BSS and identity mappings. The loader validates and decodes the image in
   RAM, builds the final FDT and uses the exact vendor kernel SMC.

The private fixed-width record contains a magic/version/size, generation,
detected RAM bytes, native timestamps, model identity, selected DRAM type,
the trained 32-word vendor parameter block, board bytes, Android image length
and integrity checks. Addresses come from the generated layout, never from
untrusted record pointers. A shared C header and static layout assertions
define the ABI for both compilers. There are no function pointers, U-Boot
objects, allocators or borrowed runtime state in the record.

One builder owns the media layout, generated model policy and both firmware
stages. SPL owns native SD, recovery, PMIC/DRAM, immutable preload and monitor
entry. The A32 binary owns its runtime, bounded Android/BOL4 parsing, final FDT
and kernel entry; it links no MMC or U-Boot runtime. Reuse the existing bounded
LZ4 decoder and pinned upstream libfdt. A single generated model policy owns
legacy DRAM names, panel rules and environment-derived bootargs.

The current fixed destinations remain: kernel `0x40080000`, initrd
`0x42000000`, final FDT `0x44000000`, staged Android image `0x45000000`,
monitor `0x48000000`, BL33 `0x4a000000`, DTBO `0x4a100000`, monitor DTB
`0x4a200000`, record `0x4a300000`, temporary EL3 tables `0x4a400000`.
BL33's linker reserves its own stack and A32 tables inside its one-MiB slot.
Prove non-overlap against actual monitor reads/writes and reservations. Never
assume `0x49000000` is free. SPL code remains below `0x2c000`, BSS below
`0x34000`; code size is a build gate, not permission to grow SRAM use.

The finished native unit reads compact monitor/DTB/DTBO/loader metadata at
p4+40 MiB and the Android image at p4+48 MiB, leaving original TOC1 and gzip
boot.img intact. Reading the original TOC before replacing its BL33 in RAM is
allowed during bring-up only. Step 2's library blob and A/B policy remain out
of this implementation unit.

### DRAM and final FDT contract

The mandatory graft from the A64 candidate is trained geometry normalization.
The vendor boot0 size and rank/width instructions show that the prepared bank
may describe 1 GiB while mainline detects 2 GiB. Copy the selected 32 words,
then set word 6 to `(3<<12)|(rows<<4)|cols`, and word 7 to
`(size_MiB<<16)|(two_ranks?0x1000:0)|(half_width?1:0)`. The same normalized
array feeds BL31, the record and Linux. Reject unsupported/asymmetric geometry
or a size that disagrees with the detected configuration. The linked native
encoder has passed 480 real vendor size-routine checks across both boot0
binaries; physical suspend/resume acceptance remains required.

Use the prepared TOC1 DTB as the kernel base, embedding those exact bytes in
the native Android payload. A complete property census on both live boards
matches that tree on all seven differences from the older Android-embedded
tree: five late USB statuses, auto_print disabled and debug_mode zero. The
initial embedded-tree assumption was incorrect. The native loader still owns
the final FDT it actually passes to Linux. Preserve every unmodified
property, the first-24-word legacy `/dram` name order, 64-bit memory base/size,
initrd extents and late USB behavior. Leave the HDMI node's stock `okay`
status alone (see "HDMI on native starts" below). Derive serial from the vendor SID
words and panel choice from model policy: RG28 old, Cube boe, others PE6.
Generate environment-derived fixed bootargs and preserve dynamic values from
their actual hardware sources. The no-logo path advertises no boot framebuffer
or `disp_reserve`. Stamp generation and stage timings in the final kernel FDT.
The final call is A32 `r0=0x8000ff04`, `r1=0x40080000`, `r2=0x44000000`,
`r3=1`, with interrupts masked and caches/MMU cleaned and disabled.

### HDMI on native starts

1.5.1 and 1.5.2 set the HDMI node's status to `late` in the tree they passed
to Linux, carried over from the earlier fast-path prototype, but no release
shipped anything to start it later. On every native start HDMI did not exist:
no extcon state for the frontend to read, so a plugged-in TV did nothing.
Stock-loader starts kept `okay` and worked, so whether a unit had HDMI depended
on which loader started it. A module that starts the device later is not
enough: the vendor display driver registers its HDMI output during its own
startup only if the status then reads exactly `okay` (`disp_init_hdmi`), and
the HDMI driver's later registration only looks up that existing output.

The loader now leaves the prepared tree's `okay` in place, and `native/verify.py`
requires it. The stock kernel's HDMI start took about 3.7 ms in an instrumented
boot (`evidence/kernel-initcalls-full.dmesg`).

### Zero-tuned LPDDR4 banks (1.5.2)

Field reports on 1.5.1 showed RG40XX H and RG CubeXX units falling back to
the stock loader on every start (about 4.45 s before Linux instead of about
1.46 s). Both were 1 GiB LPDDR4 units with layout 0x30fa/0x04000000. The lab
RG28XX has the same memory, and the cause was reproduced and bisected on it.

Every model's factory boot0 carries a generic LPDDR4 bank (at boot0 offset
0xa74) whose tpr11 and tpr12 words are zero. Vendor boot0 searches for those
DQ delays itself; mainline training uses them directly. With zero delays the
PHY training still reports success, the size probe sees no aliasing and picks
cols 11 / rows 17 (8 GiB), and `mctl_calc_size` then reads base + 6 GiB,
outside the DRAM window. The SPL hangs there. A hang never falls through to
the next bank, so the 16 s watchdog starts stock. Bounding the PHY polls does
not help.

RG28XX trials (warm restarts, one unit; each row changes one thing):

| Candidate list | Result | Before Linux |
|---|---|---:|
| Generic bank as shipped | stock after the watchdog | 4.52 s |
| + RG28XX's own tpr6/tpr11/tpr12 | fast | 1.46 s |
| + mainline H700 tpr11/tpr12 only | fast | 1.46 s |
| Full RG40XX H list (two LPDDR3 banks, then generic + mainline delays) | fast | 1.48 s |
| + RG28XX para1/para2/tpr13, delays still zero | stock | 4.53 s |
| Generic bank, then generic + mainline delays, polls bounded | stock, second bank never ran | 4.53 s |

So tpr11/tpr12 decide it; tpr6, para1, para2 and tpr13 do not. LPDDR3 banks
tried first on an LPDDR4 unit fail fast, in about 22 ms.

The 1.5.2 SPL changes two things:

1. At build time, any LPDDR4 bank with tpr11 = tpr12 = 0 gets the U-Boot
   v2026.10 H700 defaults that ROCKNIX also ships, 0x1b1f1e1c / 0x06060606, in
   the training table and in the copy passed to BL31. Tuned banks are not
   changed (`spl/test-dram-candidates.py`).
2. Before `mctl_calc_size`, the SPL refuses a layout whose probe would land
   above 0xfffffffc (more than 2 GiB; this covers every cols 11 + rows 17
   result). It then writes a 4-word pattern at base and halfway, checks it and
   restores the contents. Either failure moves on to the next bank instead of
   hanging. A hypothetical 3-4 GiB H700 unit would start the slow stock way.

Effect per model: the rg28xx candidate table is byte-identical to 1.5.1 (its
fallbacks are DDR3). The other ten models change only the generic LPDDR4
bank's words 28/29. rg34xxsp, rg35xxh, rg35xxpro, rg40xxh, rg40xxv, rgcubexx
and rgsp have no other LPDDR4 bank, so their LPDDR4 units depended on it. The
lab RG28XX ran the production 1.5.2 loader fast (1.458-1.460 s) over warm
restarts, sleep and a cold power-on, and a test list with the zero bank first
showed the size check moving on to the filled bank (1.476-1.483 s). No RG40XX
H or RG CubeXX has run it yet.

### Recovery and acceptance

The independent judge's useful second graft is a second pinned stock source,
owned by SRAM recovery and read serially through the same fixed buffer. It
mitigates source corruption, not a torn primary boot0 write. The latter is
not power-loss atomic, and BROM fallback to sector 512 remains unproved.

Persistent installation must avoid rewriting boot0 on each healthy boot.
The selected policy uses an exclusively owned RTC pending word: clear permits
one native attempt; pending/corrupt restores stock before DRAM and resets.
Only Linux with the matching generation clears it after bounded health
acceptance while watchdog recovery remains active. RTC ownership, persistence
through reset/monitor/suspend, and injected fallback must pass first. Until
then restore-first one-shot boots prove bring-up only, not durable installation.

Acceptance executes actual linked SPL/MMC, monitor, A32 loader, decoder,
libfdt and final SMC instructions. Explicitly inspect translation descriptors,
cache-maintenance ordering and PoC visibility; Unicorn alone does not model
cache correctness. Generate/replay all eleven model policies and candidate
banks. Physical acceptance on both connected boards requires a new boot ID,
matching final-FDT generation, expected memory/panel/serial and health plus
suspend/resume. Automated BROM restarts are not physical button cold boots.
Only after correctness repeat matched complete-boot benchmarks, commit and
install durable step 1, then begin step 2.

The geometry implementation is accepted by the parent against the binary
contract and real-instruction cross-check. Cache preload, own BL33, complete
native Linux and RTC recovery are still implementation/proof work; no native
boot-time reduction is claimed by this design selection.

### Early Linux entry diagnosis

The shared premise now under test is: matching Image/FDT bytes and the four
argument registers is sufficient to reproduce the vendor Linux entry contract.
Hardware reaches the A64 entry probe (stage49) but still falls back before
native Linux/ADB. Do not compensate with more guessed initialization. The
rerunnable `native/census-fdt.py` compares the actual working no-logo chain,
prepared TOC, old embedded tree and candidate FDT before another functional
fix. Remaining boundaries are physical cache visibility, complete architectural
state and earlier bootloader hardware setup. The vendor monitor's actual
instructions already set CNTHCTL_EL2=3, CNTVOFF_EL2=0 and HCR_EL2.RW for A64;
there is no evidence yet for adding a second timer initialization.
