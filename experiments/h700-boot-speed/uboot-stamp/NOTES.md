# rg34xxsp U-Boot counter stamps

`boot-package-stamped.bin` is `work/rg34xxsp/boot-gzip/boot-package.bin` (sha256
32f92785..., the package the build ships and the device has now) with six counter
stamps added to the U-Boot item. Each stamp redirects one existing `bl` to a stub. The stub
stores CNTPCT (64-bit, 24 MHz, `mrrc p15,0,r0,r1,c14`, the same instruction as the vendor's
own `__udelay` at 0xD64) and then runs `b.w` to the original target. The stub saves and
restores r0-r3. Flags, r4-r12, lr and sp are not changed. Every stamp is passed through
unchanged.

Patched package sha256: `370a44c86eca27fcff1d02d3016c489fdf6599d7608c8abb1580e46634c485a3`.
Rebuild: `/tmp/uboot-venv/bin/python work/rg34xxsp-stamp/build-stamped.py`; verify:
`/tmp/uboot-venv/bin/python work/rg34xxsp-stamp/verify-stamped.py` (writes verify-output.txt).

## Deviation from the brief: the 0xFF padding cannot hold code

The 0xFF run at 0xFDAA0..0x100000 is real, but it is not free at run time:

- The relocate_code literals at 0x13C4..0x13D0 are __image_copy_start 0x4A000000,
  __image_copy_end 0x4A0E6450, __rel_dyn_start 0x4A0E6450 and __rel_dyn_end 0x4A0FDAA0.
  .rel.dyn (11978 R_ARM_RELATIVE entries) ends exactly where the padding starts.
- crt0 `_main` (ARM code, 0xF40) does the following after relocate_code (0x136C): it runs
  `memset(__bss_start, 0, __bss_end - __bss_start)` at 0xF94..0xFA4, with literals
  0xFC4 = 0x4A0E6450 and 0xFC8 = 0x4A133848. .bss overlays .rel.dyn and runs to 0x4A133848.
  The whole padding is therefore zeroed before board_init_r, and 46 relocated pointers refer
  to .bss variables inside it. Examples: 0x4A0FDAF4 is loaded at 0x59EB8 and 0x4A0FDB20 at
  0x5B404. If U-Boot relocates, the padding is not even copied, because the copy stops at
  0xE6450.
- Code placed there would have been zeroed and then overwritten with variables. The
  emulation harness would still have passed it, because it does not run crt0. The device
  would not have booted.

Where the stubs are instead: the long help text of the `sunxi_nand_test` command, at
.rodata 0x92FD7. It is 2119 bytes long and lies inside the copied image. The stubs use 177
bytes, from 0x92FD7 to 0x93088. Checks made by build-stamped.py:

- Of all 11978 relocated pointers, the only one into this text is the help field of the
  command's cmd_tbl entry, 0xE6224.
- Nothing refers into the text PC-relative: no Thumb ldr/adr (16- or 32-bit) and no ARM
  ldr. The scan over-approximates by treating every halfword as code.
- No BL or B.W targets the text or the padding.
- The text is read only by `help sunxi_nand_test`, and from cmd_usage when that command is
  given wrong arguments. Its first byte is now NUL, so the help prints as empty.
- The stubs hold no image-absolute addresses. Their branches are PC-relative and their
  literals point to DRAM 0x40000xxx, so relocation, if it happens, does not affect them.

## Stamps

| slot | call site | original target | meaning |
|---|---|---|---|
| 0 | 0x2B22 | 0x2AA8 | board_late_init: the logo routine 0x2A2C returned. Also writes the magic and clears slots 1-5 |
| 1 | 0xF13E | 0xEA90 | run_main_loop calls main_loop: the vendor post-logo steps are done |
| 2 | 0xC1FC | 0x0FE0 | do_sunxi_flash, first BL (memset of its local buffer): the card read starts |
| 3 | 0x7E80 | 0xE8F8 | do_bootm, first BL (env_get "boot_from_partion"): the card read is done and bootm starts |
| 4 | 0x152A | 0x13FC | do_bootm_linux (0x1514), OS_PREP branch, calls boot_prep_linux: the kernel is unpacked |
| 5 | 0x1482 | 0xE8F8 | boot_jump_linux, env_get("machid"): about to jump to the kernel |

Corrections to the routine map, all checked against the binary:

- The first BL in do_sunxi_flash goes to memset 0xFE0, not to blk_get_dev.
- The first BL in do_bootm goes to env_get 0xE8F8. The vboot-screen call is the third BL,
  at 0x7E94.
- boot_jump_linux begins at 0x1470 (`ldr.w r3,[sb]` before the push at 0x1474).
- do_bootm_states does not call boot_prep_linux directly. It calls do_bootm_linux 0x1514
  through a pointer, and do_bootm_linux calls boot_prep_linux at 0x152A (the OS_PREP state,
  the one taken) or at 0x1544 (neither PREP nor GO set; not patched).
- If a stamped routine runs more than once in a boot, its slot keeps the time of the last
  run.

Memory layout: the magic word 0x5354414D ("STAM") is at 0x400000F8. Slot n is at
0x40000100 + 8n, low word first. 0x400000FC is not written. On the device this is page
`dd if=/dev/mem bs=4096 skip=262144 count=1`: the magic is at +0xF8 and the slots start at
+0x100.

## Listings (capstone) and verification output

```
## call sites (before -> after)
slot 0    2b22: fff7c1ff  bl #0x4a002aa8       ->  90f059fa  bl #0x4a092fd8
slot 1    f13e: fff7a7fc  bl #0x4a00ea90       ->  83f067ff  bl #0x4a093010
slot 2    c1fc: f4f7f0fe  bl #0x4a000fe0       ->  86f014ff  bl #0x4a093028
slot 3    7e80: 06f03afd  bl #0x4a00e8f8       ->  8bf0def8  bl #0x4a093040
slot 4    152a: fff767ff  bl #0x4a0013fc       ->  91f095fd  bl #0x4a093058
slot 5    1482: 0df039fa  bl #0x4a00e8f8       ->  91f0f5fd  bl #0x4a093070

## stubs
stub 0 at 0x92fd8 (board_late_init: logo routine 0x2A2C returned (call of 0x2AA8))
   92fd8: 0fb4       push {r0, r1, r2, r3}
   92fda: 51ec0e0f   mrrc p15, #0, r0, r1, c14
   92fde: 0a4a       ldr r2, [pc, #0x28]
   92fe0: 0a4b       ldr r3, [pc, #0x28]
   92fe2: 1360       str r3, [r2]
   92fe4: 4ff00003   mov.w r3, #0
   92fe8: 9060       str r0, [r2, #8]
   92fea: d160       str r1, [r2, #0xc]
   92fec: 1361       str r3, [r2, #0x10]
   92fee: 5361       str r3, [r2, #0x14]
   92ff0: 9361       str r3, [r2, #0x18]
   92ff2: d361       str r3, [r2, #0x1c]
   92ff4: 1362       str r3, [r2, #0x20]
   92ff6: 5362       str r3, [r2, #0x24]
   92ff8: 9362       str r3, [r2, #0x28]
   92ffa: d362       str r3, [r2, #0x2c]
   92ffc: 1363       str r3, [r2, #0x30]
   92ffe: 5363       str r3, [r2, #0x34]
   93000: 0fbc       pop {r0, r1, r2, r3}
   93002: 6ff751bd   b.w #0x4a002aa8
   93006: 00bf       nop 
   93008: f8000040   .word 0x400000f8
   9300c: 4d415453   .word 0x5354414d
           (r2 <- 0x400000f8)
           (r3 <- 0x5354414d)
stub 1 at 0x93010 (run_main_loop: call of main_loop (vendor post-logo steps done))
   93010: 0fb4       push {r0, r1, r2, r3}
   93012: 51ec0e0f   mrrc p15, #0, r0, r1, c14
   93016: 034a       ldr r2, [pc, #0xc]
   93018: 1060       str r0, [r2]
   9301a: 5160       str r1, [r2, #4]
   9301c: 0fbc       pop {r0, r1, r2, r3}
   9301e: 7bf737bd   b.w #0x4a00ea90
   93022: 00bf       nop 
   93024: 08010040   .word 0x40000108
           (r2 <- 0x40000108)
stub 2 at 0x93028 (do_sunxi_flash: first BL (memset of its local buffer): card read starts)
   93028: 0fb4       push {r0, r1, r2, r3}
   9302a: 51ec0e0f   mrrc p15, #0, r0, r1, c14
   9302e: 034a       ldr r2, [pc, #0xc]
   93030: 1060       str r0, [r2]
   93032: 5160       str r1, [r2, #4]
   93034: 0fbc       pop {r0, r1, r2, r3}
   93036: 6df7d3bf   b.w #0x4a000fe0
   9303a: 00bf       nop 
   9303c: 10010040   .word 0x40000110
           (r2 <- 0x40000110)
stub 3 at 0x93040 (do_bootm: first BL (env_get boot_from_partion): card read done, bootm starts)
   93040: 0fb4       push {r0, r1, r2, r3}
   93042: 51ec0e0f   mrrc p15, #0, r0, r1, c14
   93046: 034a       ldr r2, [pc, #0xc]
   93048: 1060       str r0, [r2]
   9304a: 5160       str r1, [r2, #4]
   9304c: 0fbc       pop {r0, r1, r2, r3}
   9304e: 7bf753bc   b.w #0x4a00e8f8
   93052: 00bf       nop 
   93054: 18010040   .word 0x40000118
           (r2 <- 0x40000118)
stub 4 at 0x93058 (do_bootm_linux(OS_PREP): call of boot_prep_linux: kernel unpacked)
   93058: 0fb4       push {r0, r1, r2, r3}
   9305a: 51ec0e0f   mrrc p15, #0, r0, r1, c14
   9305e: 034a       ldr r2, [pc, #0xc]
   93060: 1060       str r0, [r2]
   93062: 5160       str r1, [r2, #4]
   93064: 0fbc       pop {r0, r1, r2, r3}
   93066: 6ef7c9b9   b.w #0x4a0013fc
   9306a: 00bf       nop 
   9306c: 20010040   .word 0x40000120
           (r2 <- 0x40000120)
stub 5 at 0x93070 (boot_jump_linux: env_get(machid): about to jump to the kernel)
   93070: 0fb4       push {r0, r1, r2, r3}
   93072: 51ec0e0f   mrrc p15, #0, r0, r1, c14
   93076: 034a       ldr r2, [pc, #0xc]
   93078: 1060       str r0, [r2]
   9307a: 5160       str r1, [r2, #4]
   9307c: 0fbc       pop {r0, r1, r2, r3}
   9307e: 7bf73bbc   b.w #0x4a00e8f8
   93082: 00bf       nop 
   93084: 28010040   .word 0x40000128
           (r2 <- 0x40000128)

## byte diff
207 bytes differ; every one lies in: package checksum (word 20) (4), U-Boot checksum (item word 3) (4), help text NUL + stubs (177), call site 0x2b22 (4), call site 0xf13e (4), call site 0xc1fc (4), call site 0x7e80 (3), call site 0x152a (4), call site 0x1482 (3)
the 0xFF padding 0xFDAA0..0x100000 and the monitor, dtbo and dtb items are unchanged; both checksums valid

## emulation (tests/test-fast-boot-emulation.py machinery)
fast image 7237632 bytes sha256 21f8001711fa870c (byte-identical to the build's boot.img)
CNTPCT under Unicorn: two reads gave ['0x44dd75a60d73', '0x44dd75b0c652']; native counter used
unpatched, stock harness stop: 3 s; 7237632 bytes in 2 reads (64 + 14072 sectors); reached the kernel jump; 17686536 bytes at 0x40080000 are the original kernel; 512 bytes copied once, 0x456c4800 -> 0x42000000, nothing else written there; environment initrd_start..initrd_end 0x456c4800..0x456c4a00 (512); device tree linux,initrd-start..end 0x42000000..0x42000200 (512), 512 bytes reserved there; bootargs ends '.rcu_normal_after_boot=1'
stamped, stock harness stop at 0x1470, console 3: 3 s; same jump {'flag': 1024, 'entry': 1074266112, 'fdt': 1140850688}, same card reads, bootargs and device tree as unpatched; 7237632 bytes in 2 reads (64 + 14072 sectors); reached the kernel jump; 512 bytes copied once, 0x456c4800 -> 0x42000000, nothing else written there; bootargs ends '.rcu_normal_after_boot=1'
    stub/target order: stub2 target@0xfe0 stub3 target@0xe8f8 target@0xfe0 target@0xe8f8 target@0xe8f8 target@0xe8f8 target@0xe8f8 target@0xe8f8 stub4 target@0x13fc target@0xe8f8 target@0xe8f8 target@0xe8f8
    low DRAM writes (pc, address, value): 0x93030:0x40000110=0x7f9c832c, 0x93032:0x40000114=0x44dd, 0x93048:0x40000118=0x7fa2ec0b, 0x9304a:0x4000011c=0x44dd, 0x93060:0x40000120=0x87e848f7, 0x93062:0x40000124=0x44dd
    slots: ['0x0', '0x0', '0x44dd7f9c832c', '0x44dd7fa2ec0b', '0x44dd87e848f7', '0x0']
stamped, stock harness stop at 0x1470, console 0: 3 s; same jump {'flag': 1024, 'entry': 1074266112, 'fdt': 1140850688}, same card reads, bootargs and device tree as unpatched; 7237632 bytes in 2 reads (64 + 14072 sectors); reached the kernel jump; 512 bytes copied once, 0x456c4800 -> 0x42000000, nothing else written there; bootargs ends '.rcu_normal_after_boot=1'
    stub/target order: stub2 target@0xfe0 stub3 target@0xe8f8 target@0xfe0 target@0xe8f8 target@0xe8f8 target@0xe8f8 target@0xe8f8 target@0xe8f8 stub4 target@0x13fc target@0xe8f8 target@0xe8f8 target@0xe8f8
    low DRAM writes (pc, address, value): 0x93030:0x40000110=0x896db6d0, 0x93032:0x40000114=0x44dd, 0x93048:0x40000118=0x8972ee04, 0x9304a:0x4000011c=0x44dd, 0x93060:0x40000120=0x91befa1f, 0x93062:0x40000124=0x44dd
    slots: ['0x0', '0x0', '0x44dd896db6d0', '0x44dd8972ee04', '0x44dd91befa1f', '0x0']
stamped, stop at 0x1486 (stub 5 included), console 3: 3 s; same jump {'flag': 1024, 'entry': 1074266112, 'fdt': 1140850688}, same card reads, bootargs and device tree as unpatched; 7237632 bytes in 2 reads (64 + 14072 sectors); reached the kernel jump; 512 bytes copied once, 0x456c4800 -> 0x42000000, nothing else written there; bootargs ends '.rcu_normal_after_boot=1'
    stub/target order: stub2 target@0xfe0 stub3 target@0xe8f8 target@0xfe0 target@0xe8f8 target@0xe8f8 target@0xe8f8 target@0xe8f8 target@0xe8f8 stub4 target@0x13fc target@0xe8f8 target@0xe8f8 target@0xe8f8 stub5 target@0xe8f8
    low DRAM writes (pc, address, value): 0x93030:0x40000110=0x933f2a6a, 0x93032:0x40000114=0x44dd, 0x93048:0x40000118=0x934259d8, 0x9304a:0x4000011c=0x44dd, 0x93060:0x40000120=0x9b82d409, 0x93062:0x40000124=0x44dd, 0x93078:0x40000128=0x9ce1400b, 0x9307a:0x4000012c=0x44dd
    slots: ['0x0', '0x0', '0x44dd933f2a6a', '0x44dd934259d8', '0x44dd9b82d409', '0x44dd9ce1400b']
stamped, stop at 0x1486 (stub 5 included), console 0: 3 s; same jump {'flag': 1024, 'entry': 1074266112, 'fdt': 1140850688}, same card reads, bootargs and device tree as unpatched; 7237632 bytes in 2 reads (64 + 14072 sectors); reached the kernel jump; 512 bytes copied once, 0x456c4800 -> 0x42000000, nothing else written there; bootargs ends '.rcu_normal_after_boot=1'
    stub/target order: stub2 target@0xfe0 stub3 target@0xe8f8 target@0xfe0 target@0xe8f8 target@0xe8f8 target@0xe8f8 target@0xe8f8 target@0xe8f8 stub4 target@0x13fc target@0xe8f8 target@0xe8f8 target@0xe8f8 stub5 target@0xe8f8
    low DRAM writes (pc, address, value): 0x93030:0x40000110=0x9cfec470, 0x93032:0x40000114=0x44dd, 0x93048:0x40000118=0x9d016d59, 0x9304a:0x4000011c=0x44dd, 0x93060:0x40000120=0xa54036eb, 0x93062:0x40000124=0x44dd, 0x93078:0x40000128=0xa6a2b4ad, 0x9307a:0x4000012c=0x44dd
    slots: ['0x0', '0x0', '0x44dd9cfec470', '0x44dd9d016d59', '0x44dda54036eb', '0x44dda6a2b4ad']

## stubs 0 and 1 called directly
stub 0: target 0x2aa8 reached once with r0-r3 ['0x11111111', '0x22222222', '0x33333333', '0x44444444'] and sp 0x4ffff000 unchanged, returned 0x1234 to the caller; memory from 0x400000f8: 5354414d aaaaaaaa a6bc0ac2 000044dd 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000
stub 1: target 0xea90 reached once with r0-r3 ['0x11111111', '0x22222222', '0x33333333', '0x44444444'] and sp 0x4ffff000 unchanged, returned 0x1234 to the caller; memory from 0x400000f8: aaaaaaaa aaaaaaaa aaaaaaaa aaaaaaaa a6bdce6b 000044dd aaaaaaaa aaaaaaaa aaaaaaaa aaaaaaaa aaaaaaaa aaaaaaaa aaaaaaaa aaaaaaaa

ALL CHECKS PASSED
```

Notes on the emulation:

- Runs: the unpatched run, then the stamped item with the harness's normal stop at
  boot_jump_linux entry 0x1470, with the console at stock level and switched off. Both
  stamped runs give the same jump record {flag 0x400, entry 0x40080000, fdt 0x44000000}
  as the unpatched run. They also give the same card reads, the same original kernel
  bytes, the same ramdisk handling, and the same bootargs in the environment and in the
  device tree.
- Stamp 5 lies past the harness stop. A third pair of runs moves the stop to 0x1486, just
  after the call at 0x1482, and takes the jump record from r5/r4 (images and flag). The
  record is the same there too.
- Unicorn runs the mrrc and returns a counter that increases with host time. Stamps 2 < 3
  < 4 (< 5) in every run. The values are emulator time, not device time.
- Every write to 0x40000000..0x40000FFF came from a stub instruction, and only to the slots
  expected. The unpatched run wrote nothing in that page.
- Each stub's next traced event is its own original target.
- Stubs 0 and 1 are not on the emulated path, so they were called directly:
  - The target was replaced by a model. It was reached once, with r0-r3 and sp unchanged,
    and its return value came back to the caller.
  - Stub 0 wrote the magic and its counter and cleared slots 1-5 over a 0xAA fill. It left
    0x400000FC alone.
  - Stub 1 wrote only slot 1.

What the emulation does not cover:

- Boot0, crt0, board_init_r, the logo, and the vendor steps between the logo and main_loop.
  No code in those steps was found that writes to 0x40000000..0x40000FFF. Note, though,
  that board_init (0x25DE) sets gd->bd->bi_boot_params to 0x40000100, the same address as
  STAMP_BASE. That field is the ATAG address. boot_prep_linux 0x13FC on this FDT path
  writes no tags.
- The logo's `fatload ... 40000000 bootlogo.bmp` loads the BMP file to 0x40000000 before
  stub 0 runs. Stub 0 then overwrites 56 bytes of that file buffer, after the logo has
  been drawn.
- Linux hands 0x40000000..0x4007FFFF to its page allocator. Read the page soon after boot.
  A missing magic word means stub 0 did not run, or the page has been reused.
- cleanup_before_linux (0xC7C -> 0xC3E) runs after stamp 5 and turns the caches off with a
  flush, so the stamps reach DRAM before the kernel starts.

## On the device

- `overlay/usr/sbin/baseos-boot-migrate` returns at once when /data/boot-pair-v2/complete
  exists. Without that marker, it treats this package as unknown firmware and skips
  without writing. So it does not overwrite the stamped package.
- The boot partition (fast boot.img) is unchanged and stays paired with this package.

Write it to the card in a reader on the Mac. Find N with `diskutil list`, then run
`diskutil unmountDisk /dev/diskN`:

    sudo dd if=work/rg34xxsp-stamp/boot-package-stamped.bin of=/dev/rdiskN bs=4096 seek=4100 conv=notrunc
    sudo dd if=/dev/rdiskN bs=4096 skip=4100 count=320 | shasum -a 256   # expect 370a44c8...85a3

4100 x 4096 = 16793600 and 320 x 4096 = 1310720. To restore, use the same command with
`work/rg34xxsp/boot-gzip/boot-package.bin` (32f92785...7e).

Read the stamps after boot (read-only):

    work/rg34xxsp-stamp/read-stamps.py --adb -s SERIAL --pre-kernel "BaseOS boot stages: pre-kernel X s"
    # or: adb shell 'dd if=/dev/mem bs=4096 skip=262144 count=1 2>/dev/null | od -An -tx4 -v' > dump.txt
    #     work/rg34xxsp-stamp/read-stamps.py dump.txt --pre-kernel X
