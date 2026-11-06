# T1/T2 and reset-correction host verification

Current recovery candidate: `work/mainline/firmware/build-u2r`, two patches.
BL31 is 41,060 bytes, SHA256
`46848d664393e5cbe41d138721015619fb7fc88f527e118598db96663f91612a`.
ELF is 73,608 bytes, SHA256
`13be9619ac9b21a9124d29d377d7c399f9e9273326063666549fff101834ee12`.
Entry, load extents, BSS and end remain within the original secure arena.

The 200-instruction bounded check of the actual reset function rejects the
failed U2 BL31 below and accepts the corrected candidate: CFG=1, MODE=1,
CTRL=0x14af, then the one-second delay. Evidence is
`work/mainline/firmware/{u2-reset-negative-control,u2r-reset-sequence}.json`.
GIC is skipped and MMIO is RAM, so no watchdog-counter/reset proof is claimed.
The three existing handoff tests and strict cached builder rerun pass.
No device operation occurred for this correction. Software review and both
attended hardware gates in README remain outstanding. U2 physical automatic
recovery failed; the overall goal is incomplete.

## Historical first U2 build (superseded)

Official TF-A v2.15.0 commit `da738d5eae93af342fdc4995dd3c05acb4c9d757`,
pristine tree `9b8ab09105e08b5d3593813ec1442e28720e3c00`. One BaseOS patch,
GCC 13.3.0 from the pinned toolchain image. Host only; NOT deployable.

Final output: `work/mainline/firmware/build-u2`.
`validation.json.inputs` records source identity/pins, ABI, queue, patched-content,
builder, make/compiler/toolchain hashes and build policy for generation collection.
`work/mainline/firmware/u2-verification.json` records bounded G1/G2 evidence;
`build-u2-disassembly.txt` is the actual final ELF disassembly. Two fresh final
outputs had byte-identical ELF, binary and validation JSON. The secondary output
and its log were removed. Cache reuse checks source/output contents before make.
Baseline `build` and pristine checkout remain unchanged.

- ELF: 73,608 bytes, SHA256
  `8aed4598a9f28598d21f9d61a48b5ea1a197bc99dc2830a3a5112ebfb128617b`.
- BL31 binary: 41,060 bytes, SHA256
  `6adc030f2a63a98ac173feb35d20e1e4a66362cc9ad9bb52c72af2932a060bef`.
- ELF entry `0x40000000`; PT_LOAD at `0x40000000` has file/memory size
  40,960/40,960; PT_LOAD at `0x4000a000` has 100/32,768. Memory end
  `0x40012000`, BSS `0x4000b080..0x4000b620`, all inside the 256 KiB arena.
  Reserve the entire `0x40000000..0x40040000` region.

Actual instructions (early/platform setup inlined into `bl31_main` by upstream
optimization): `0x4000102c..0x40001038` checks x0's saved value against
`0x4a300400` and branches to ERROR/panic before the first descriptor load.
`0x4000103c..0x40001058` copies exactly 256 bytes using aligned 8-byte accesses.
The private descriptor occupies `0x4000b448..0x4000b548` in secure BSS.
`0x40001240..0x4000127c` sets Linux PC `0x40200000`, SPSR `0x3c9`,
x0 FDT and x1..x3 zero. `0x40001644` calls full bounded FDT validation before
upstream PMIC detection. The old U-Boot DT search is absent from source/ELF.

The sole source patch changes setup, FDT preparation/private prototypes,
H616 include routing and H616 L2 fixup error propagation. Upstream native PM
and H616 power sources are byte-identical to pristine. Allwinner source review
finds WDOG stores only in `sunxi_system_reset` and no RTC GP writes. Actual
reset instructions `0x40007b98..0x40007b9c` store 1 to WDT CFG/MODE at
`0x030090b4`/`0x030090b8`; startup has no feed/disable operation. The trial
compatible refusal occurs before bus initialization. These are bounded source
and ELF inspections, not an executed EL3 MMIO trace.

`python3 tests/test-tf-a-handoff.py`: three tests passed, including the actual
patched FDT preparation compiled with upstream libfdt and mocked platform
fixups. Valid trees are packed/cleaned; capacity, malformed header/structure,
missing trial, PMIC compatible lists, unterminated strings and fixup errors
reject. Cache drift/unknown files and reversed patch application reject.
Parent `tests/test-u2-handoff.c` passed unchanged: valid 1/2 GiB, 256 corrupted
bytes and semantic boundary rejections. No duplicated descriptor validator suite.

No hardware/device access or full BL31 Unicorn attempt. Parent owns final helper,
SPL and capsule join/review/deployment; use `--tfa-build
work/mainline/firmware/build-u2`. T3 PMIC restart/poweroff and T4 suspend remain
unimplemented. The full goal remains incomplete.

Parent integration review corrected the trial marker lookup from the DT root to
`/chosen/baseos,trial`, requiring an empty boolean. The component test now rejects
a root-only marker and also consumes the actual U1 diagnostic DTB. All three
tests pass. Two fresh builds of these corrected inputs match byte-for-byte; a
cached rerun passes source/output-content validation. Hashes above are refreshed
for the corrected artifact. No hardware test follows from this result.
