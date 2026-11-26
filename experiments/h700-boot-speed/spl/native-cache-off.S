.section .text.baseos_native_cache_off,"ax"
.global baseos_native_cache_off
.type baseos_native_cache_off, %function
baseos_native_cache_off:
    stp x19, x30, [sp, #-16]!
    mrs x19, sctlr_el3
    /* The pinned upstream routine cleans every data/unified level to PoC. */
    bl __asm_flush_dcache_all
    bic x19, x19, #1
    bic x19, x19, #4
    bic x19, x19, #0x1000
    dsb sy
    msr sctlr_el3, x19
    isb
    tlbi alle3
    ic iallu
    dsb sy
    isb
    ldp x19, x30, [sp], #16
    ret
