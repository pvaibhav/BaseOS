.syntax unified
.thumb
.section .text.entry,"ax",%progbits
.global capture_entry
.thumb_func
capture_entry:
    push.w {r0-r12, lr}
    mrs r4, apsr
    push {r4, r5}
    bl capture_log
    pop {r4, r5}
    msr APSR_nzcvq, r4
    pop.w {r0-r12, lr}
    b.w vendor_cleanup
