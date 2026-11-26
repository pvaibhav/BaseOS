.syntax unified
.arm
.section .text.save,"ax",%progbits
.global save_entry
save_entry:
    /* Vendor startup overwrites r0-r2; preserve every other register and flags. */
    mrs r0, cpsr
    ldr r2, =0x4f0fffbc
    str r0, [r2]
    ldr r2, =0x4f0fffc0
.macro constant slot, value
    ldr r0, =\value
    str r0, [r2, #(4 * \slot)]
.endm
.macro memory slot, address
    ldr r0, =\address
    ldr r1, [r0]
    str r1, [r2, #(4 * \slot)]
.endm
    constant 0, 0x4c4b5342
    constant 1, 0x3130474f
    constant 2, 1
    constant 3, 0x4000
    constant 4, 0x4116a7f0
    memory 5, 0x41167a3c
    memory 6, 0x41167a58
    memory 7, 0x0700013c
    memory 8, 0x41167a40
    memory 9, 0x41167a44
    memory 10, 0x41167a60
    memory 11, 0x41167a64
    constant 12, 0
    constant 13, 0
    constant 14, 0
    constant 15, 0
    ldr r0, =0x4116a7f0
    ldr r2, =0x4f100000
1:  ldr r1, [r0], #4
    str r1, [r2], #4
    tst r2, #0x4000
    beq 1b
    ldr r2, =0x4f0fffbc
    ldr r0, [r2]
    msr cpsr_f, r0
    b vendor_start
    .ltorg
