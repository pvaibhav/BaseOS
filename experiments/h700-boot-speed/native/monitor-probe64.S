/* Diagnostic-only; replaces the previous entry/ERET marks with one entry mark. */
.section .text,"ax"
.global monitor_psci_trace
monitor_psci_trace:
    /* Non-secure copies of x16/x17 have already been saved at this boundary. */
    mov x16, #0x13c
    movk x16, #0x700, lsl #16
    mov w17, w0
    tbz x1, #63, 1f
    sub x16, x16, #4
    mov w17, w1
1:  str w17, [x16]
    dsb sy
    stp x29, x30, [sp, #-0x20]!
    b psci_resume
