.syntax unified
.arch armv7-a
.arm
.text
.global dram_checkpoint
.type dram_checkpoint,%function
dram_checkpoint:
    ldr r0, =dram_retry_context
    stmia r0, {r4-r11, sp, lr}
    mov r0, #0
    bx lr

/* All callers are within one bounded DRAM attempt, with caches disabled. */
.global dram_panic
.type dram_panic,%function
dram_panic:
    ldr r0, =dram_retry_context
    ldmia r0, {r4-r11, sp, lr}
    mov r0, #1
    bx lr

.bss
.balign 8
dram_retry_context:
    .space 40
