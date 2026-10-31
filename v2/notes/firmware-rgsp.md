# RG SP firmware candidate

The build target is selected at build time. This is the same known-hardware
boundary as the released BaseOS 1.x RG SP path: the stock RG SP banks and its
two PE6 panel branches. PE6 is not a panel identification protocol, and the
AXP717 SPL driver has no useful chip-ID check. This candidate cannot recognize
all unknown hardware revisions. No voltages were invented or changed.

Build:

```
docker build -t baseos-v2-firmware-clang:local -f v2/firmware/Containerfile .
python3 v2/firmware/build.py v2-rgsp --target rgsp
python3 v2/os/build.py payload --firmware v2-rgsp --dtb work/v2/kernel/rgsp.dtb
python3 v2/firmware/tests/run.py v2-rgsp
python3 v2/firmware/write-install.py
```

Helper and capsule cross compilation for RG SP run in the Linux compiler
image by immutable local image ID; compiler binary hashes and that ID are in
the generation identity. SPL and BL31 retain the pinned existing toolchain.
Container setup may download Linux packages. Actual compilation/tests run
without network. Nothing is installed on the Mac host.

`targets.json` pins the stable model numbers and stock identities and checks
policy output against geometry, panel selection and allocation. `layout.py`
checks the framebuffer reservation against both RAM sizes and all policy arenas.
The new generation covers the target generators, stock files, policy inputs,
compiled sources, patches and compiler identities. Legacy RG28XX generation
calculation is intentionally unchanged for byte compatibility.

RG SP uses helper-owned SPI startup (v2a-ab style). A v2-local patch validates
the independent DTB root compatible before helper panel initialization and
rejects DRAM record types outside the RG SP stock LPDDR3/LPDDR4 set. Another
v2-local source transformation bounds the two retained read-calibration loops,
unwinding through the existing SRAM candidate retry mechanism on timeout.
No shared files in experiments/ need edits.

The RG SP DTB already supplies `/chosen/framebuffer@0` (libfdt accepts the
unit-address-free lookup), disabled, 720x480, stride 2880, x8r8g8b8, zero reg;
chosen/ranges, two-cell addresses and sizes, chosen/baseos,trial, memory,
secure reservation and PSCI SMC. The shared finalizer writes the same chosen
metadata as RG28XX and fills/enables simplefb. No kernel changes were made.

RG SP payload selection uses `--firmware v2-rgsp`, selecting its generation,
DTB default, logo and private output directory. Without that option the existing
v2a generation and RG28XX paths remain the defaults. A DTB override alone does
not change generation. Restamping cannot turn an RG28XX DTB into an RG SP DTB.

Offline results:

- Exact legacy comparison: work/v2/firmware/v2a-rebuilt/comparison.json.
  Existing e1/v2a binary outputs and generation files were not replaced.
- Legacy regression suite: work/v2/firmware/tests.json and legacy-tests.log.
- RG SP chain/capsule emulation: work/v2/firmware/v2-rgsp/tests.json.
- Compiled DRAM-selector emulation: work/v2/firmware/build/v2-rgsp/spl/selection-verification.json.
- Owner handoff with exact hashes, offsets, stock identity, write order and
  ordered device checks: work/v2/firmware/v2-rgsp/INSTALL.md.

The BOE stream ends with a delay; its trace check measures that final wait
through TCON enable rather than assuming another SPI frame follows.

Emulation covers both PE6 levels, the three stock bank records, invalid DRAM
type, absent logo, wrong generation and wrong-board DTB. Missing logo retains
the existing black-frame success behavior. Refusal leaves pending intact; the
separate capsule emulator proves nonzero RTC selects stock on the following
entry. A physical watchdog/reset or a usable picture is not proved by this.
The selector harness models training, checks retries/timeouts and memory/geometry
refusals, and records the voltage requests. It does not measure electrical
behavior or run full physical training. Frozen real counters, unknown PMIC
identity, every training poll, Linux watchdog continuity and late health
acknowledgment are not qualified by this candidate. RTC is still cleared early
by the existing OS rcS.

No handheld access, flashing, backups, commits or pushes. Owner staging belongs
in device /tmp and must be removed; preserve all user content in place.
