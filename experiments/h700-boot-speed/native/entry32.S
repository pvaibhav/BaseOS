.syntax unified
.arch armv7-a
.arch_extension sec
.arm
.section .text.entry,"ax",%progbits
.global native_entry
native_entry:
    b native_runtime
    .space 0x1000 - 4
native_runtime:
    cpsid if
#ifdef BASEOS_NATIVE_DIAGNOSTIC
    ldr r0, =0x0700013c
    ldr r1, =0x42530028
    str r1, [r0]
    dsb sy
#endif
    ldr sp, =0x4a0f0000
    ldr r0, =__bss_start
    ldr r1, =__bss_end
    mov r2, #0
1:  cmp r0, r1
    strlo r2, [r0], #4
    blo 1b
    bl baseos_loader_boot
2:  wfi
    b 2b

.global native_enable_mmu
native_enable_mmu:
    dsb sy
    mov r0, #0
    mcr p15, 0, r0, c2, c0, 2
    mcr p15, 0, r0, c8, c7, 0
    mcr p15, 0, r0, c7, c5, 0
    ldr r0, =0x4a0f8000
    mcr p15, 0, r0, c2, c0, 0
    mov r0, #1
    mcr p15, 0, r0, c3, c0, 0
    mrc p15, 0, r0, c1, c0, 0
    bic r0, r0, #0x30000000
    /* SCTLR.A clear: the kernel decoder uses unaligned LDR/STR on Normal DRAM. */
    bic r0, r0, #2
    orr r0, r0, #0x00800000
    orr r0, r0, #1
    orr r0, r0, #4
    orr r0, r0, #0x1000
    dsb sy
    isb
    mcr p15, 0, r0, c1, c0, 0
    isb
    bx lr

.global native_ticks
native_ticks:
    mrrc p15, 0, r0, r1, c14
    bx lr

.global native_kernel_enter
native_kernel_enter:
    cpsid if
    mrc p15, 0, r9, c1, c0, 0
    /* Same set/way clean as the previously verified A32 handoff. */
    mrc p15, 1, r0, c0, c0, 1
    ands r3, r0, #0x07000000
    mov r3, r3, lsr #23
    beq 5f
    mov r4, #0
1:  add r2, r4, r4, lsr #1
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
2:  mov r11, r7
3:  orr r12, r4, r5, lsl r6
    orr r12, r12, r11, lsl r2
    mcr p15, 0, r12, c7, c14, 2
    subs r11, r11, #1
    bge 3b
    subs r5, r5, #1
    bge 2b
4:  add r4, r4, #2
    cmp r4, r3
    blt 1b
5:  dsb sy
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
#ifdef BASEOS_NATIVE_DIAGNOSTIC
    ldr r0, =0x0700013c
    ldr r1, =0x42530030
    str r1, [r0]
    dsb sy
#endif
    movw r0, #0xff04
    movt r0, #0x8000
#ifdef BASEOS_NATIVE_DIAGNOSTIC
    ldr r1, =native_kernel_trampoline
#else
    ldr r1, =0x40080000
#endif
    ldr r2, =0x44000000
    mov r3, #1
    smc #0
6:  wfi
    b 6b
#ifdef BASEOS_NATIVE_DIAGNOSTIC
    .balign 8
.global native_kernel_trampoline
.type native_kernel_trampoline, %object
native_kernel_trampoline:
#include "native-trampoline.inc"
.size native_kernel_trampoline, . - native_kernel_trampoline
#endif
