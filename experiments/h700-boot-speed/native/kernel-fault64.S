/* Diagnostic-only vectors in the vendor Image's unused idmap-page padding. */
.section .text,"ax"
.global kernel_fault_start
kernel_fault_start:
    mrs x10, elr_el1
    /* The vector is identity mapped. Drop translation before RTC access. */
    dsb sy
    mrs x11, sctlr_el1
    bic x11, x11, #1
    bic x11, x11, #4
    bic x11, x11, #0x1000
    msr sctlr_el1, x11
    isb
    mov x9, #0x13c
    movk x9, #0x700, lsl #16
    str w10, [x9]
    dsb sy
1:  wfi
    b 1b
    .space 0x1c0 - (. - kernel_fault_start)
.global kernel_fault_vectors
kernel_fault_vectors:
.rept 16
    b kernel_fault_start
    .space 124
.endr
