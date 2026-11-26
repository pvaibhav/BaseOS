/* Resume-only prototype. Link/copy to 0x50000 and branch there at EL3.
 * Precondition: verified stock 64 KiB at 0x38000, SCTLR_EL3.M/C clear.
 * This path must be selected before native clocks, PMIC or DRAM setup.
 * No stack or DRAM data is consumed; SP is moved out of both copy regions.
 */
.section .text,"ax"
.global stock_resume64
stock_resume64:
    mrs x9, CurrentEL
    cmp x9, #12
    b.ne fail
    msr daifset, #15
    mrs x9, sctlr_el3
    tbnz x9, #0, fail
    tbnz x9, #2, fail
    bic x9, x9, #0x1000
    dsb sy
    msr sctlr_el3, x9
    isb
    mov x9, #0x7000
    movk x9, #5, lsl #16
    mov sp, x9
    mov x0, #0x38000
    mov x1, #0x20000
    mov x2, #0x30000
copy:
    ldp x4, x5, [x0], #16
    stp x4, x5, [x1], #16
    cmp x1, x2
    b.ne copy
    dsb sy
    ic iallu
    tlbi alle3
    dsb sy
    isb
    mov x0, #0x24
    movk x0, #0x300, lsl #16
    ldr w1, [x0]
    and w1, w1, #7
    mov x3, #0x40
    movk x3, #0x901, lsl #16
    cmp w1, #2
    b.ne rvbar
    mov x3, #0x40
    movk x3, #0x810, lsl #16
rvbar:
    mov w0, #0x20000
    str w0, [x3]
    str wzr, [x3, #4]
    dsb sy
    mrs x2, rmr_el3
    bic x2, x2, #1
    orr x2, x2, #2
    msr rmr_el3, x2
    isb
fail:
    wfi
    b fail
