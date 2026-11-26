.section .text.entry,"ax"
.global kernel_trace
kernel_trace:
    /* Diagnostic-only entry probe. Preserve the kernel ABI in x0..x3. */
    mov x9, #0x13c
    movk x9, #0x700, lsl #16
    mov w10, #49
    movk w10, #0x4253, lsl #16
    str w10, [x9]
    dsb sy
    mov x20, x0
    mov x21, x1
    mov x22, x2
    mov x23, x3
    mov x9, #0x4a0d0000
    mov sp, x9
    bl native_check_entry
    cbnz w0, failed
    mov x9, #0xd800
    movk x9, #0x4098, lsl #16
    msr vbar_el1, x9
    isb
    mov x9, #0x13c
    movk x9, #0x700, lsl #16
    mov w10, #51
    movk w10, #0x4253, lsl #16
    str w10, [x9]
    dsb sy
    mov x0, x20
    mov x1, x21
    mov x2, x22
    mov x3, x23
    mov x9, #0x40080000
    br x9
failed:
    mov x9, #0x13c
    movk x9, #0x700, lsl #16
    movk w0, #0x4253, lsl #16
    str w0, [x9]
    dsb sy
1:  wfi
    b 1b
