.syntax unified
.arch armv7-a
.arm
.section .header,"a",%progbits
.incbin "capsule-header.bin"
.section .text.entry,"ax",%progbits
.global entry, saved_context, refuse
entry:
 str r0,saved_context
 adr r0,saved_context
 stmib r0,{r1-r14}
 mrs r1,cpsr
 str r1,[r0,#60]
 mrc p15,0,r1,c1,c0,0
 str r1,[r0,#64]
 ldr r2,=0x1005
 tst r1,r2
 bne refuse
 ldr r1,[r0,#60]
 and r2,r1,#0x1f
 cmp r2,#0x13
 bne refuse
 tst r1,#0x200
 bne refuse
 mov r1,#0x20000
 ldrb r1,[r1,#0x28]
 str r1,[r0,#68]
 cpsid if
 ldr sp,=0x57000
 bl capsule_main
 cmp r0,#0
 beq refuse
 mov r1,#0x21000
 cmp r0,r1
 moveq r1,#0x10000
 beq selected
 mov r1,#0x31000
 cmp r0,r1
 bne refuse
 mov r1,#0xc000
selected:
 dsb sy
 isb sy
 ldr pc,=0x50000
refuse:
 b refuse
.ltorg
saved_context:
.space 72
.section .copy,"ax",%progbits
.global copy_start,copy_end
copy_start:
 mov r2,#0x20000
1:
 ldr r3,[r0],#4
 str r3,[r2],#4
 subs r1,r1,#4
 bne 1b
 ldr r0,=0x52000
 ldr r1,[r0,#68]
 mov r2,#0x20000
 strb r1,[r2,#0x28]
 dsb sy
 isb sy
 ldr r1,[r0,#60]
 msr cpsr_fsxc,r1
 ldmib r0,{r1-r14}
 ldr r0,[r0]
 ldr pc,=0x20000
.ltorg
copy_end:
