# Counter stamps in the vendor U-Boot: RG34XX SP, 2026-10-15

The v2 package (six stamps, block copied into the device tree tail) booted and
read back. Counter seconds from the origin, one boot:

| point | at | delta |
|---|---:|---:|
| logo routine returned | 3.381 | |
| vendor post-logo steps done, main_loop | 3.481 | 99.5 ms |
| card read of the boot image starts | 3.487 | 6.5 ms |
| card read done (7.25 MB), bootm | 3.791 | 304 ms = 24 MB/s |
| kernel unpacked, boot_prep_linux | 4.179 | 388 ms |
| kernel jump | 4.220 | 41 ms |
| kernel clock origin (boot log pre-kernel) | 4.394 | 174 ms |

After the logo: 1.01 s, as the emulation predicted (panel, card, gunzip,
monitor plus early kernel). Before the logo routine returns: 3.38 s (boot0,
DRAM training, U-Boot init, logo load). The v3 package with ten more stamps on
the init_sequence_r table booted but its block was not found in the tree (not
investigated; the first stub may clear the block after the kernel-side copy
expectation, or the table stubs run before .bss is cleared, which wipes the
scratch block at 0x400000F8 if it lies in a region U-Boot zeroes). The card
was restored to the build's package afterwards (sha 32f92785...).
