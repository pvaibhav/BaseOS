.text
.global dram_checkpoint
.type dram_checkpoint,%function
dram_checkpoint:
    adrp x0, dram_retry_context
    add x0, x0, :lo12:dram_retry_context
    stp x19, x20, [x0]
    stp x21, x22, [x0, #16]
    stp x23, x24, [x0, #32]
    stp x25, x26, [x0, #48]
    stp x27, x28, [x0, #64]
    stp x29, x30, [x0, #80]
    mov x1, sp
    stp x1, x18, [x0, #96]
    mov x0, #0
    ret

.global dram_panic
.type dram_panic,%function
dram_panic:
    adrp x0, dram_retry_context
    add x0, x0, :lo12:dram_retry_context
    ldp x19, x20, [x0]
    ldp x21, x22, [x0, #16]
    ldp x23, x24, [x0, #32]
    ldp x25, x26, [x0, #48]
    ldp x27, x28, [x0, #64]
    ldp x29, x30, [x0, #80]
    ldp x1, x18, [x0, #96]
    mov sp, x1
    mov x0, #1
    ret

.bss
.balign 16
dram_retry_context:
    .space 112
