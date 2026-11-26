# How the vendor boot chain tells hardware revisions apart

October 2026. Anbernic ships one firmware per model, and one model exists in
several hardware revisions: different DRAM (the RG34XX SP on the bench has
2 GB where the RG SP has 1 GB) and different panels. ROCKNIX ships separate
device trees for such revisions and leaves the choice to the user. Before
BaseOS replaces any part of the boot chain, this survey records what the
vendor chain detects at run time, where, and how, so that a replacement can
do the same. Static analysis of the prepared firmware of all eleven targets,
checked against two devices.

The scripts here read `work/<target>/boot-prefix.img` and
`work/<target>/boot-gzip/` as the build prepares them. `boot-chain-survey.py`
compares the boot0 headers, lists the boot package items and greps the U-Boot
builds. `kernel-strings.py` unpacks every kernel into `work/variant-survey/`
and greps it. `panel-survey.py` lists the panel names each part knows.
`strap-disasm.py <target> [string]` disassembles the U-Boot code around a
string. The `.txt` files are their output.

## DRAM: boot0 tries candidate parameter sets

boot0's code is byte-identical on all eleven targets. Its header holds a
primary DRAM parameter block at 0x38 and a table of alternates near 0xa00.
boot0 trains with candidates until one succeeds, and hands the winning
block to U-Boot through the Allwinner header at the start of the U-Boot item,
which it fills in RAM. U-Boot copies the block into the `/dram` node of the
device tree. The kernel's standby code reads it from there.

| Target | Primary set | Alternates in the header |
|---|---|---|
| rg28xx | LPDDR4 at 672 MHz | DDR3 at 792 MHz |
| rg34xx, rg35xxplus, rg35xxsp | LPDDR4 at 672 MHz | LPDDR3, LPDDR4 |
| rg34xxsp, rg35xxh, rg35xxpro, rg40xxh, rg40xxv, rgcubexx, rgsp | LPDDR3 at 672 MHz | LPDDR3, LPDDR4 |

Proof on the RG34XX SP: its header's primary set is LPDDR3 (`dram_para1`
0x1919, `dram_para2` 0x9988eeee), but the running kernel's `/dram` node holds
type 8 (LPDDR4) with `dram_para1` 0x0e0e and `dram_para2` 0x7887bbbb, the
alternate set.

The fresh lab inventory also confirms LPDDR3 at 672 MHz on the RG40XX V,
with about 1 GiB before kernel reservations. The RG34XX SP has 2 GiB LPDDR4.
`compatibility.py` generates the complete parameter and panel contract in
`../evidence/compatibility.json`. It reads all eleven prepared firmware images,
both vendor PE6 traces, and pinned live inventories. The old RG28XX primary-type
row was incorrect. Its prepared header contains LPDDR4 as the primary set and
DDR3 as an alternate.

## Panel: U-Boot reads one GPIO strap

Every U-Boot build carries a panel detection written by the vendor (the
`--allen--PE_data` messages). In the RG SP, RG40XX V and RG35XX H builds the
code, read by disassembly:

1. sets the function of PE6 in `PE_CFG0` (0x0300B090),
2. enables the pull-up of PE6 in `PE_PULL0` (0x0300B0AC) (the RG40XX V build
   skips this step),
3. reads `PE_DAT` (0x0300B0A0) and sets `lcd_type` to 1 when bit 6 is low,
4. branches to one of two panel init sequences, and
5. puts `lcd_type=boe` or `lcd_type=HSD` into the kernel command line. Some
   builds also know the name `JDI`.

The kernel's panel driver, named per target in the device tree
(`fog_fj035fhd05_v1`, `rg34xx_v1`, `rg34xxsp_v1`), picks its init sequence
from that token. The kernel also parses `lcd_type=` itself and its panel code
maps the PIO registers, so it may read the strap on its own; nobody has
checked what it does when the token is missing. The RG28XX and CubeXX builds
print `lcd_type` as well, but their strap read was not located.

The completed traces emit `old` for PE6 high and `boe` for PE6 low on nine
models. RG28XX emits `old` at both levels, while CubeXX emits `boe` at both.
A shared PE6-to-token rule would change RG28XX and CubeXX behavior. Other
detection inputs still need review before claiming every physical panel revision.

## Other hardware settings

Keys, sticks, the second USB port of the RG35XX H and Pro, and the battery
capacity are static per target in the device tree and kernel. The `dtbo` item
of the boot package is Allwinner's reference overlay for an AXP1530 PMIC and
carries no board information. The RG34XX SP's and RG40XX V's device trees
enable the on-chip ADC for their sticks; the RG SP's does not.

## What a replacement bootloader must do

The current user request replaces boot0 as well as U-Boot. The retained-boot0
design below describes the measured checkpoint. A complete replacement also
needs automatic DRAM type, rank, width, and size detection from SRAM. The
generic mainline LPDDR4 configuration alone does not meet that requirement.

The vendor U-Boot is 32-bit code that boot0 loads together with the monitor
(BL31) and the device tree from the TOC1 package, and that enters the arm64
kernel through one monitor call (`boot_jump_linux`, offset 0x1470 in the RG SP
build). A replacement that keeps boot0 and the monitor keeps DRAM detection
and sleep as they are. It must:

- carry the Allwinner `uboot` header so boot0 fills in the DRAM block, and
  copy that block into `/dram` and the memory size into `/memory`;
- read PE6 as above and emit `lcd_type=`;
- build the command line the kernel expects: the `partitions=` map, the
  serial number, `bootreason` from the PMIC for the charger-only boot, and
  `disp_reserve` only if it draws a logo;
- read the kernel and jump through the monitor as the vendor code does.
