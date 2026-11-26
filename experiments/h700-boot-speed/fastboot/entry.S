.syntax unified
.arch armv7-a
.thumb
.section .text.entry,"ax",%progbits
.global unpack_kernel
.thumb_func
unpack_kernel:
    push {r4, lr}
    ldr r4, =0x344c4f42
    ldr ip, [r2]
    cmp ip, r4
    bne fallback_branch
#ifdef TRIAL_WATCHDOG
    ldr r4, =0x030090a0
    mov.w ip, #1
    str ip, [r4, #0x14]
    mov.w ip, #0xb1
    str ip, [r4, #0x18]
    movw ip, #0x14af
    str ip, [r4, #0x10]
    dmb sy
#endif
    pop {r4, lr}
    b.w custom_unpack
.global fallback_branch
fallback_branch:
    pop {r4, lr}
.global fallback_jump
fallback_jump:
    b.w .                 /* Builder redirects this to the original gunzip. */

.macro vendor_veneer name
.global \name
.thumb_func
\name:
    b.w .
.endm
vendor_veneer vendor_phyread
#ifdef QUICK_RECOVERY
vendor_veneer vendor_phywrite
#endif
#ifdef PANEL_TRIAL
vendor_veneer vendor_env_get
vendor_veneer vendor_env_set
vendor_veneer vendor_env_save
vendor_veneer vendor_logo
vendor_veneer vendor_reserve
#ifdef LATE_HDMI
vendor_veneer vendor_fdt_path
vendor_veneer vendor_fdt_getprop
.global vendor_fdt_blob
.thumb_func
vendor_fdt_blob:
    ldr.w r0, [r9, #0x5c]
    bx lr
#endif
#endif
