/* In-memory checkpoints only; the pinned TOC1 on card remains unchanged.
 * H616 manual section 3.10.4.11: GP_DATA_REG15 at RTC + 0x13c.
 * The monitor exit hook runs before its original x16/x17 restore, so later
 * AArch64 kernel calls retain their entire register context.
 */
static u32 trace_branch(u32 from, u32 to, bool arm32)
{
    return (arm32 ? 0xea000000u : 0x14000000u) |
        (((to - from - (arm32 ? 8 : 0)) >> 2) & (arm32 ? 0x00ffffffu : 0x03ffffffu));
}

static void trace_monitor(void)
{
    /* One first-return hook fits in the zeroed reserved header, within BL31's
     * executable first page. Ordinary DRAM is execute-never under its MMU. */
    volatile u32 *stub = (void *)0x480000c0;
    const u32 code[] = {
        0x52802790, /* mov w16, #0x13c */
        0x72a0e010, /* movk w16, #0x700, lsl #16 */
        0xb9400211, /* ldr w17, [x16] */
        0x12003e31, /* and w17, w17, #0xffff */
        0x71008a3f, /* cmp w17, #34 */
        0x54000081, /* b.ne original_restore */
        0xd53e4031, /* mrs x17, elr_el3 */
        0xb9000211, /* str w17, [x16] */
        0xd5033f9f, /* dsb sy */
        0xa94847f0, /* ldp x16, x17, [sp, #0x80] */
        0xd69f03e0, /* eret */
    };
    for (unsigned i = 0; i < sizeof(code) / sizeof(code[0]); i++) stub[i] = code[i];
    /* Keep the NS checkpoint in BL33's own reserved header. A separate
     * DRAM address may belong to the monitor's secure carve-out. */
    volatile u32 *arm = (void *)0x4a000620;
    arm[0] = 0xe59fc00c; /* ldr ip, [pc, #12] */
    arm[1] = 0xe59fe00c; /* ldr lr, [pc, #12] */
    arm[2] = 0xe58ce000; /* str lr, [ip] */
    arm[3] = 0xf57ff04f; /* dsb sy */
    arm[4] = trace_branch(0x4a000630, 0x4a000640, true);
    arm[5] = 0x0700013c;
    arm[6] = 0x42530025; /* stage 37: BL33 entry */
    *(volatile u32 *)0x480159d0 = trace_branch(0x480159d0, 0x480000c0, false);
    *(volatile u32 *)0x4a000000 = trace_branch(0x4a000000, 0x4a000620, true);
    asm volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
}
