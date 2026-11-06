/* EL3 only. All exception slots deliberately wait for the inherited watchdog. */
.section .text.entry,"ax"
.global helper_entry
helper_entry:
 msr daifset, #15
 msr spsel, #1
 mov x9, #0x4a300000
 cmp x0, x9
 b.ne helper_fault
 mov x9, #0x4a0f0000
 mov sp, x9
 adr x9, helper_vectors
 msr vbar_el3, x9
 isb
 adr x9, __bss_start
 adr x10, __bss_end
1: cmp x9, x10
 b.hs 2f
 str xzr, [x9], #8
 b 1b
2: bl helper_main
 b helper_fault
.balign 2048
helper_vectors:
.rept 16
 b helper_fault
 .balign 128
.endr
.global helper_fault
helper_fault:
 wfi
 b helper_fault
.global native_ticks
native_ticks:
 mrs x0, cntpct_el0
 ret
.global helper_mmu_on
helper_mmu_on:
 mov x17, x30
 bl helper_cache_all
 mov x30, x17
 mov x9, #0xff00
 msr mair_el3, x9
 ldr x9, =0x80803520
 msr tcr_el3, x9
 ldr x9, =0x4a0f8000
 msr ttbr0_el3, x9
 isb
 tlbi alle3
 ic iallu
 dsb sy
 isb
 mrs x9, sctlr_el3
 orr x9, x9, #1
 orr x9, x9, #4
 orr x9, x9, #0x1000
 msr sctlr_el3, x9
 isb
 ret
.global helper_exit
helper_exit:
 /* No loads, stores or stack use from this point through the BL31 branch.
  * Disable allocation first; CISW then cleans all produced data to PoC. */
 dsb sy
 mrs x18, sctlr_el3
 bic x18, x18, #1
 bic x18, x18, #4
 bic x18, x18, #0x1000
 msr sctlr_el3, x18
 isb
 bl helper_cache_all
 ic iallu
 tlbi alle3
 dsb sy
 isb
 mov x0, #0x4a300000
 add x0, x0, #0x400
 mov x1, xzr
 mov x2, xzr
 mov x3, xzr
 mov x9, #0x40000000
 br x9
/* H700 Cortex-A53, legacy CCSIDR (no FEAT_CCIDX). Same level/set/way
 * construction as the pinned U-Boot cache.S; CLZ must use W, not X. */
helper_cache_all:
 dsb sy
 mrs x10, clidr_el1
 ubfx x11, x10, #24, #3
 mov x0, #0
 cbz x11, 5f
1: add x12, x0, x0, lsl #1
 lsr x12, x10, x12
 and x12, x12, #7
 cmp x12, #2
 b.lt 4f
 lsl x12, x0, #1
 msr csselr_el1, x12
 isb
 mrs x6, ccsidr_el1
 ubfx x2, x6, #0, #3
 add x2, x2, #4
 ubfx x3, x6, #3, #10
 ubfx x4, x6, #13, #15
 clz w5, w3
2: mov x6, x3
3: lsl x7, x6, x5
 orr x9, x12, x7
 lsl x7, x4, x2
 orr x9, x9, x7
 dc cisw, x9
 subs x6, x6, #1
 b.ge 3b
 subs x4, x4, #1
 b.ge 2b
4: add x0, x0, #1
 cmp x0, x11
 b.lt 1b
5: msr csselr_el1, xzr
 dsb sy
 isb
 ret
