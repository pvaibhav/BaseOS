.syntax unified
.arch armv7-a
.arch_extension sec
.arm
.section .text.entry,"ax",%progbits
.global handoff_entry
handoff_entry:
    cpsid if
    mov r8, r0
    mrc p15, 0, r9, c1, c0, 0
    mrs r10, cpsr
    /* Clean and invalidate each data/unified cache before leaving DRAM. */
    mrc p15, 1, r0, c0, c0, 1
    ands r3, r0, #0x07000000
    mov r3, r3, lsr #23
    beq 5f
    mov r4, #0
1:
    add r2, r4, r4, lsr #1
    mov r1, r0, lsr r2
    and r1, r1, #7
    cmp r1, #2
    blt 4f
    mcr p15, 2, r4, c0, c0, 0
    isb
    mrc p15, 1, r1, c0, c0, 0
    and r2, r1, #7
    add r2, r2, #4
    ubfx r5, r1, #3, #10
    clz r6, r5
    ubfx r7, r1, #13, #15
2:
    mov r11, r7
3:
    orr r12, r4, r5, lsl r6
    orr r12, r12, r11, lsl r2
    mcr p15, 0, r12, c7, c14, 2
    subs r11, r11, #1
    bge 3b
    subs r5, r5, #1
    bge 2b
4:
    add r4, r4, #2
    cmp r4, r3
    blt 1b
5:
    dsb sy
    bic r0, r9, #1
    bic r0, r0, #4
    bic r0, r0, #0x1000
    mcr p15, 0, r0, c1, c0, 0
    isb
    mov r0, #0
    mcr p15, 0, r0, c8, c7, 0
    mcr p15, 0, r0, c7, c5, 0
    dsb sy
    isb
    mov r1, r8
    movw r0, #0xff04
    movt r0, #0x8000
    mov r2, #0
    mov r3, #1
    smc #0
6:  wfi
    b 6b
