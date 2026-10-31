# BaseOS 2 firmware across the H700 models

Research date: 2026-10-31. Read-only source and prepared-image investigation.
No handheld was contacted. No flashing, backups, commits, pushes, host package
installation, or changes to existing e1/v2a outputs were made.

## Decision

Use one source implementation with **per-model capsules and payloads first**,
starting with RG SP. Keep runtime DRAM and panel selection within each model.
Do not distribute the RG28XX capsule as universal firmware. BaseOS 1.x supplies
useful per-model contracts, not a universal physical model detector.

I stopped at the plan. An RG SP build requires more than changing `TARGET`:
the framebuffer generator rejects its layout, the current tests assume an
RG28XX card/payload, and the strict unknown-hardware rule is not implemented.
The expected `work/v2/kernel/rgsp.dtb` was absent at inspection time. Building a
binary with those gaps would not establish an RG SP-capable safe boot path.

The largest limitation is fundamental: a one-bit panel strap always returns a
known-looking answer, even on an unknown panel. Training success similarly
does not prove that an unseen RAM revision is electrically qualified. Neither
this investigation nor a successful offline build can promise support for all
future hardware sold under these model names.

## 1. What v2a assumes about the RG28XX

References below are to the current worktree, including its existing uncommitted
changes. Line numbers identify the relevant implementation, not historical
release source. The existing installation document was read:
`work/v2/firmware/v2a/INSTALL.md`.

| Area | Actual assumption and source |
|---|---|
| Inputs and target | `v2/firmware/build.py:64–73` fixes the inventory directory, handoff package and `TARGET='rg28xx'`. `:225–228` calls the native model-policy generator with those fixed inputs. There is no target CLI option. |
| Stock fallback | `build.py:479–515` embeds that inventory's entire 64 KiB stock boot0, copies its first 1 KiB into the outer capsule header, and hashes the embedded stock copy. This is part of recovery, not an interchangeable blob. |
| DRAM candidate banks | `build.py:390–418` derives the SPL's candidates from that RG28XX boot0. `experiments/h700-boot-speed/spl/build-early-mmc.py:31–48` reads 32-word banks at 0x38, 0x9f4, 0xa74, deduplicates them, and replaces zero LPDDR4 TPR11/12 with 0x1b1f1e1c/0x06060606. RG28XX gets LPDDR4/672 MHz then DDR3/792 MHz; it does **not** get the RG SP's LPDDR3 banks. Full words are in the audit JSON. |
| DRAM detection implementation | `spl/dram-select.c:25–40,105–142` normalizes trained geometry and tries candidates; `:57–92` checks the address window and a small low/high memory sample. `build-early-mmc.py:61–90` selects PHY map0/map1 from SID at runtime. These mechanisms are shared, but their input banks are model-specific. |
| Supply voltages | `spl/dram-select.c:121–125` changes DCDC3 to 1100/1200/1500 mV for LPDDR4/LPDDR3/DDR3 before training. v2a's pinned `anbernic_rg35xx_h700_defconfig` also enables AXP717, DCDC2=940 mV, initial DCDC3=1100 mV, R-I2C/400 kHz, and a status GPIO numbered 268. The configuration name is not evidence of all-model compatibility. `build.py:116–119,444–453` selects it. |
| Panel choice | The generated RG28XX rule is fixed `old` (rule 0), no PE6 pull-up. RG SP is rule 2: PE6 high=`old`, low=`boe`, pull-up enabled. Policy comes from `native/build.py:125–145` and `native/panel-contract.json:27,173`. |
| Power/reset/SPI pins | `spl/native-boot.c:76–150` uses PI15 power, PI14 reset, minimum waits 30/15/50/100 ms, then the model's strap rule. `native/panel-spi9.h:29–45` uses PI8 chip select, PI9 clock, PI10 data. These are shared implementation assumptions backed by the vendor contracts, not physical revision identification. |
| Early panel commands | `v2/firmware/patches/early-panel.patch:114–205` moves the command stream into the SPL/card-read overlap; `:206–315` carries and validates the continuation in the helper. `build.py:378–385` emits only the model's applicable streams. RG28XX has only its `old` stream; RG SP has two different streams (`native/panel-init.json:10,70`). |
| Display clocks, mux, timings, backlight | `native/build-display.py:31–77,80–110` selects model-specific `prepare/publish/enable/backlight` operation lists and panel streams, checked against pinned vendor package/DTB hashes. RG28XX operations start at `native/display-engine.json:4`; RG SP starts at `:27994`. Reusing just the RG28XX SPI commands or changing only logo dimensions is insufficient. |
| Logo geometry and rotation | RG28XX has physical 480×640 pixels, with artwork rotation 90° CCW in `devices.json:5–14`. RG SP has 720×480 and 0° at `devices.json:125–136`. `native/build.py:79–109` supplies dimensions/allocation; `build.py:357–365` compiles them into SPL. `native/early-display.c:133–146` decodes the physical BMP; it does not apply the device-profile rotation itself. A missing logo produces black pixels, then still initializes the panel. |
| Framebuffer allocation | RG28XX framebuffer is RAM end minus **68012032**; RG SP uses **68007936**. `experiments/h700-mainline/u3-join/build-helper.py:23–27` asserts the RG28XX value. `v2/firmware/build.py:307–308` calls it. This is a concrete target-change build failure. `u3/simplefb.c:31–87` then validates geometry, stride, RAM and reservations against the compiled layout. |
| Card geometry | `native/build.py:59–75,89–91` extracts and validates GPT boot and boot-resource locations against the prepared inventory. `build.py:362–374` compiles absolute LBAs into SPL. Available inventories have p4 at 303104, but runtime code does not discover arbitrary layouts. Firmware/payload are p4+40/48 MiB. |
| Model identity and generation | `native/build.py:134` assigns model IDs from sorted target names. `spl/native-boot.c:266–284` writes the compiled model ID into the record; it does not measure the board. `helper/helper.c:12–20` checks it against the helper's compiled ID. `build.py:239–261` binds target, policies, stock image, sources and toolchains into a generation. This catches mixed components, not a physically wrong card. |
| DTB | `spl/native-handoff.h:51–85` defines BOS3 v1 with exactly one independent mainline DTB. `helper/helper.c:26–39,69` validates the envelope/empty Android DTB and passes the independent DTB to finalization. There is no model-compatible check on that DTB today. A same-generation, wrong-board DTB is not rejected by an explicit board identity rule. |
| DTB finalization | `helper/fdt-final.c:45–79` writes detected RAM size, DRAM words, serial, panel token, boot reason, initrd and display/timing metadata. `u3-join/wrap.c:6–16` adds simplefb. These routines are mostly generic, but their required framebuffer template and compiled layout are target-specific. |
| PMIC permission | `v2/firmware/patches/helper-allow-pmic.patch:1` and `tfa-allow-pmic.patch:1` remove the x-powers ban. They do not identify a regulator revision or validate its electrical limits. Panel-related GPIO access is independent of that DTB permission. |

The vendor DTB timing fields extracted in the container are:

| Field (vendor units) | RG28XX | RG SP |
|---|---:|---:|
| lcd_x / lcd_y | 480 / 640 | 720 / 480 |
| lcd_dclk_freq (MHz) | 24 | 26 |
| lcd_ht / lcd_hbp / lcd_hspw | 606 / 66 / 6 | 820 / 46 / 2 |
| lcd_vt / lcd_vbp / lcd_vspw | 686 / 26 / 6 | 536 / 20 / 5 |

These are vendor properties, not a new measurement of panel frequency or a
replacement for the generated register operation lists.

The RTC addresses, one-try policy, watchdog, stage addresses, LZ4 decoder and
BL31 handoff are not inherently RG28XX-specific. Their execution on other
hardware still needs qualification. `native/capsule/capsule.c:79–96` chooses
stock on resume, nonzero RTC, damaged SPL or watchdog ownership conflict;
it arms the watchdog and commits pending before entering native code.

## 2. What BaseOS 1.x really detects

**Model identity is selected when preparing/building the image.** The eleven
profiles are in `devices.json`. Plus and 2024 share the `rg35xxplus` profile;
they are not twelve independent profiles. `tools/prepare_stock.py:678–695`
records the chosen profile. `build-rootfs.sh:115–139` writes exact target,
frontend family, model string, rotation and capabilities into the rootfs.
The compatibility group (`baseos_device`) is not an exact board identity:
for example RG34XX SP shares `rg34xx` with RG34XX.

The migration installer checks target identity, hashes, boot0 body and geometry
before installing (`overlay/usr/sbin/baseos-boot-migrate:144–203`). This guards
against mixing known images; it is not a physical model probe. The container
audit found **identical bytes `0054415785e995e9` at boot0 offset 0xf0 in all
eleven prepared models**. The `board[8]` field copied from there must not be
used as a universal model ID. Serial/SID also needs a proven mapping before
being used as a model identifier; the existing SID test chooses DRAM wiring.

**DRAM:** vendor boot0 tries parameter banks and publishes the trained result.
The native replacement has its own upstream trainer and retry/geometry checks.
It supports DDR3, LPDDR3 and LPDDR4 in source; this is not evidence that every
physical revision has passed. The fleet bank order and panel policy are:

| Profile | Deduplicated DRAM candidate types, in order | Panel high/low | PE6 pull-up | Physical logo |
|---|---|---|---|---|
| rg28xx | LPDDR4/672, DDR3/792 | old / old | no | 480×640 |
| rg34xx | LPDDR4, LPDDR3, LPDDR4 | old / boe | yes | 720×480 |
| rg34xxsp | LPDDR3, LPDDR3, LPDDR4 | old / boe | yes | 720×480 |
| rg35xxplus (also 2024) | LPDDR4, LPDDR3, LPDDR4 | old / boe | yes | 640×480 |
| rg35xxh | LPDDR3, LPDDR3, LPDDR4 | old / boe | yes | 640×480 |
| rg35xxpro | LPDDR3, LPDDR3, LPDDR4 | old / boe | yes | 640×480 |
| rg35xxsp | LPDDR4, LPDDR3, LPDDR4 | old / boe | no | 640×480 |
| rg40xxh | LPDDR3, LPDDR3, LPDDR4 | old / boe | no | 640×480 |
| rg40xxv | LPDDR3, LPDDR3, LPDDR4 | old / boe | no | 640×480 |
| rgcubexx | LPDDR3, LPDDR3, LPDDR4 | boe / boe | no | 720×720 |
| rgsp | LPDDR3, LPDDR3, LPDDR4 | old / boe | yes | 720×480 |

All clocks are 672 MHz except the explicitly marked DDR3 bank. Repeated types
have different parameter words; do not deduplicate by type alone.

**Panel:** the nine variable-panel models read PE6 after the power/reset
sequence; RG28XX and CubeXX are fixed choices. The pull-up is model-specific.
The survey records vendor high/low instruction traces, not a census of every
physical panel revision (`experiments/h700-boot-speed/variant-survey/README.md:44–76`).
Panel selection is a two-way electrical strap classification, not panel ID
readback. It has no third result for unfamiliar hardware.

Reliability has three distinct levels:

1. Prepared-image identity and both vendor strap branches are strong offline
   evidence for the pinned firmware inputs. They do not identify hardware.
2. Existing physical evidence is narrower. The RG SP correction record
   `experiments/h700-boot-speed/evidence/rgsp-panel-install-v2.json` reports
   one verified boot and a user-confirmed normal picture with `old`.
   `rgsp-native-daily-install.json` withdraws an earlier acceptance: software
   looked healthy while `boe` selection made the screen black. Do not count
   that earlier record as a success. The RG SP record concerns the native
   vendor-kernel/monitor path, not BaseOS 2's helper + upstream TF-A + mainline.
3. RAM training and electrical behavior cannot be established offline.
   Two unbounded calibration loops remain in the already generated v2a source:
   `work/v2/firmware/build/v2a/spl/u-boot-2026.10/arch/arm/mach-sunxi/dram_sun50i_h616.c:396,412`.
   The source transformation at `spl/build-early-mmc.py:59–94` retains those
   functions. The outer watchdog can recover from a stall, but a stall can
   prevent the next bank from being attempted. The small memory sample is
   not a full signal-integrity test.

The saved `work/fastpath-analysis/sol-report.md:7–38` in the main checkout
likewise distinguishes packaging success from hardware qualification and
records H/Cube failures with stock fallback. Its hypotheses are historical
leads, not new diagnoses. `work/boot-architecture/ASSESSMENT.md` describes
retaining vendor stages as an earlier checkpoint, not proof that upstream
TF-A preserves vendor suspend behavior.

## 3. Proposed boot and payload design

### First release: shared source, per-model files

Define explicit target records in `v2/firmware/` with stable model IDs, pinned
stock boot0/package identities, candidate banks, allowed PMIC identities and
voltages, panel rules/streams/display operations, physical dimensions, framebuffer
allocation and accepted DTB compatible strings. Never derive persistent IDs
from alphabetical order as the fleet grows. Keep existing e1/v2a generation
and output behavior intact. New target builds get new names, for example
`work/v2/firmware/v2-rgsp/` and private intermediates below that directory.

Keep BOS3 v1 for the first RG SP path. Package the separately built
`work/v2/kernel/rgsp.dtb` as its independent DTB, with the existing Android
image's required empty DTB left empty. The kernel and initramfs can be shared
inputs. Require a new target-specific generation and explicit root compatible
validation before Linux. Add a panel-profile property or equivalent checked
mapping so the detected `old/boe` choice agrees with the mainline panel driver;
confirm the actual binding with the kernel agent rather than inventing its
compatible string. RAM size and trained words remain runtime metadata.

Select a DTB variant only for differences the kernel DT cannot describe safely
in one tree. RAM size alone does not require separate DTBs: finalization already
writes detected RAM. A panel driver with two validated sequences can use the
runtime panel token. Different wiring/regulators may require separate DTBs.

Before any board-specific output or rail change, require the strongest
available identity checks. On today's boards that means a correctly selected,
qualified per-model image and pre-installation stock characterization supplied
by the owner. That is a deployment prerequisite, not runtime physical
identification. Keep unknown/ambiguous revisions in stock; a model name or
matching card hash alone must not authorize experimental electrical settings.
The open identity limitation below must be resolved before claiming the full
unknown-hardware requirement.

Keep the 16-second outer watchdog and pending marker through every failure.
Bound all training waits so bank retries can occur; bound every later polling
loop and do not repeatedly feed the watchdog on a failing path. An explicit
failure after changing hardware should request a whole-system reset with
pending retained, rather than jump directly into stock with altered DRAM and
peripheral state. If reset code itself fails, let the independent watchdog win.
Check the watchdog remains active across helper, TF-A and Linux until the
intended health acknowledgment. Early rcS clearing is too weak if the required
success condition includes a usable screen and controls.

Panel startup must dispatch the model's own generated register tables and
streams. Check a revision identity before sending commands where such an
identity is available. Preserve vendor reset waits and polarity, power sequence,
SPI mux, timing, framebuffer placement and backlight delay. For the first RG SP
build, use helper-owned initialization as in v2a-ab; defer early SPL streaming
until the ordinary sequence and mainline takeover are qualified. Disabling the
logo alone does not disable panel initialization in today's helper. A headless
trial needs an explicit separate contract and does not qualify screen support.

### Later: a common payload with a DTB directory

A multi-DTB payload is useful even if capsules stay per-model. Introduce a new
BOS3 version or new magic; do not hide a directory in v1 reserved bytes, which
the current parser requires to be zero (`native-handoff.h:84`). Proposed layout:

1. One 512-byte envelope: total length, version, generation, header/body checksum,
   Android extent, DTB-directory extent/count, zero reserved fields.
2. One Android kernel/initramfs image with the existing empty Android DTB.
3. A fixed-width directory of `(stable model ID, hardware-profile ID,
   DTB offset, DTB length, DTB checksum)` entries, followed by aligned DTB blobs.

Require exactly one matching entry, checked arithmetic, bounded count (for
example 64), no overlapping extents or duplicate keys, 512-byte total alignment,
total ≤16 MiB, and selected DTB ≤1 MiB minus finalization slack. Validate the
directory and body before selection, then validate the selected FDT and root
compatible before use. Missing or ambiguous entries fail to stock. Keep checksum
semantics explicit: current FNV detects corruption, not malicious replacement.

The runtime key comes from the capsule's qualified target plus verified variant
selection, never from trying DTBs until one boots. Update the packer, SPL
envelope checks, helper and offline tests together. The present `restamp`
command only changes a generation; it neither inserts an RG SP DTB nor converts
v1 into this format.

A truly identical capsule for every model is a separate, harder project: it
needs a reliable physical model discriminator before regulator/panel writes,
a way to choose matching stock recovery before native setup, and more space
or a validated way to locate model-specific stock boot0. The current 128 KiB
capsule contains one 64 KiB stock image and one 48 KiB SPL; it cannot simply
hold eleven stock copies. Common eGON board bytes, PE6, screen dimensions and
DRAM type do not uniquely identify the model. Defer that design until a
discriminator is demonstrated.

## 4. RG SP execution plan and open questions

1. **Resolve identity and electrical coverage.** Establish which RG SP RAM,
   PMIC and panel revisions are covered by the saved stock evidence. Is there a
   readable, trustworthy board/panel revision ID before configuring those
   outputs? If not, what explicit known-hardware deployment boundary is
   acceptable? An unseen panel with the same PE6 level is indistinguishable
   today. No software-only promise of universal unknown-panel refusal follows.
2. **Make the new build self-contained.** Add target configuration and a new
   variant under `v2/firmware/`; generate the framebuffer layout locally from
   checked policy rather than calling the RG28XX-only assertion. RG SP's
   68007936 offset must be checked against 1/2 GiB arenas and reservations.
   Include this generator and all generated-policy inputs in the generation
   hash. Move helper/capsule clang+lld compilation into the pinned container;
   current `build.py:200–210,283–340,479–509` uses host tools.
3. **Audit and bound DRAM/PMIC setup.** Use RG SP's own complete LPDDR3/LPDDR3/
   LPDDR4 banks and SID map logic. Fix retained unbounded calibration waits in
   the staged source with a v2-local patch. Verify PMIC ID before any dependent
   voltage write; audit upstream PMIC/board initialization ordering, not just
   the explicit selector writes. Establish safe trial voltages across accepted
   revisions. Unsupported identities return to stock without such writes.
4. **Join the kernel contract.** Wait for the RG SP DTB and confirm root
   compatible, AXP717 binding, PSCI SMC, secure-memory reservation, chosen trial
   marker, bootargs, 720×480/2880-byte stride disabled simplefb template, panel
   variant input and warm-display takeover. Confirm suspend/resume behavior
   separately; old vendor-monitor success does not cover upstream BL31.
5. **Replay the actual RG SP chain offline.** Generalize the existing tests
   using a private RG SP card fixture and payload, never the RG28XX fixture
   implicitly (`v2/firmware/tests/run.py:33–34`). Exercise both PE6 levels,
   missing/corrupt logo, every DRAM bank and geometry failure, unknown PMIC/
   identity refusal before writes, frozen counters/calibration, mismatched DTB,
   bad firmware/envelope, record corruption, active watchdog, resume, nonzero
   RTC and failed watchdog readback. Verify compiled GPIO/SPI trace against
   the correct vendor stream and all write addresses/voltages against policy.
   Emulated training must be labeled as emulated; it does not qualify RAM.
6. **Only then emit candidate files.** Use `v2-rgsp/INSTALL.md` in v2a's style:
   exact generation/cookie, lengths/hashes, p4 offsets and measured expected
   layout, stock recovery identity, payload requirements and explicit untested
   hardware checklist. Hold stock first; write firmware/payload before capsule;
   retain the existing secondary stock region. Explain that interrupted primary
   capsule writes can require external card repair: BROM fallback to LBA512 is
   not established by this repo's v2a notes. All owner-side staging uses `/tmp`,
   removes its exact files, and preserves games, saves, settings and credentials.
   No SD backups, extra archives, manifests or installer clutter.
7. **Owner qualification after handoff.** Cold boots and restarts for each
   accepted revision; deliberate failed attempt followed by stock fallback;
   visible logo and Linux takeover for both panels; RAM stress, buttons/lid,
   charging-only startup, shutdown/restart and suspend/resume. The owner does
   all device work. Expand to other models only after the corresponding profile
   passes the same gates.

## 5. What was actually checked

Added `v2/firmware/audit-models.py`. It reads prepared inputs only and emits JSON
to stdout. Results: `work/v2/firmware/all-models-research/audit.json`.

It ran successfully in the existing `python:3.12-slim` Docker image with network
disabled, the repo and prepared inputs mounted read-only, and bytecode disabled.
The host shell redirected stdout only into the new research output directory.
No build/compiler/package installation was done on the Mac.

Verified offline:

- All eleven prepared boot0 and TOC package SHA-256 values match the checked-in
  compatibility contract; all extracted candidate banks meet the existing
  generator's type/clock/remap requirements.
- Exact candidate words, vendor LCD fields, stream identities, physical sizes,
  strap rules, board-header bytes and available framebuffer offsets were read.
- Nine available fleet inventories pass the existing model policy and display
  contract checks, including RG SP. Those checks validate package/environment/
  prepared-prefix identities and the policy's memory-arena calculations.
- The actual `layout_header()` function succeeds or fails per model as recorded
  in the audit JSON. It fails on RG SP's framebuffer offset assertion.

Not checked: full policy for RG34XX SP/RG40XX V (absent in this particular fleet
inventory directory), new compilation/linking, new executable boot-chain replay,
new RG SP DTB integration, actual DRAM training, electrical limits on unknown
revisions, panel picture, fallback on a handheld, power/suspend behavior or boot
time. Existing hardware records are cited as historical evidence only.

Reproduce the audit from this worktree (no device access):

```sh
docker run --rm --network=none \
  -v "$PWD:/repo:ro" \
  -v /Users/pvaibhav/Code/Me/BaseOS/work:/prepared:ro \
  -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim \
  python /repo/v2/firmware/audit-models.py /prepared \
  > work/v2/firmware/all-models-research/audit.json
```

There is deliberately no firmware binary or INSTALL.md claiming an installable
RG SP build. The next concrete milestone is a container-built, RG SP-specific
candidate with its own DTB and a proved refusal/recovery contract.
