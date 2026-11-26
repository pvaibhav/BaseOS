/* One-shot omission of the U-Boot splash, with persisted normal-boot fallback. */
typedef unsigned int u32;
extern const char *vendor_env_get(const char *name);
extern int vendor_env_set(const char *name, const char *value);
extern int vendor_env_save(void);
extern int vendor_logo(void);
extern int vendor_reserve(void);
extern volatile u32 trial_sector __attribute__((visibility("hidden")));
#ifdef LATE_HDMI
extern void *vendor_fdt_blob(void);
extern int vendor_fdt_path(void *tree, const char *path);
extern void *vendor_fdt_getprop(void *tree, int node, const char *name, int *length);
#endif
#ifdef QUICK_RECOVERY
extern int trial_persist(void);
#endif

int trial_logo(void)
{
    trial_sector = 0x5a000;
    const char *flag = vendor_env_get("baseos_skip_logo");
    if (!flag || flag[0] != 'y' || flag[1] != 'e' || flag[2] != 's' || flag[3])
        return vendor_logo();
    /* Persist the normal panel path before any changed hardware behavior. */
    if (vendor_env_set("baseos_skip_logo", "no") ||
#ifdef QUICK_RECOVERY
        trial_persist())
#else
        vendor_env_save())
#endif
        return vendor_logo();
    trial_sector = 0x62000;
    volatile u32 *timer = (volatile u32 *)0x03009000;
    timer[0xb4 / 4] = 1;
    timer[0xb8 / 4] = 0xb1;
    timer[0xb0 / 4] = 0x14af;
    __asm__ volatile("dmb sy" ::: "memory");
    volatile u32 *pio = (volatile u32 *)0x0300b000;
    pio[0x90 / 4] &= ~(15u << 24);    /* PE6 input */
    pio[0xac / 4] = (pio[0xac / 4] & ~(3u << 12)) | (1u << 12);
    return vendor_env_set("lcd_type", pio[0xa0 / 4] & 0x40 ? "old" : "boe");
}

int trial_reserve(void)
{
    if (trial_sector == 0x62000) {
#ifdef LATE_HDMI
        void *tree = vendor_fdt_blob();
        int node = vendor_fdt_path(tree, "/soc@03000000/hdmi@06000000"), length = 0;
        u32 *status = vendor_fdt_getprop(tree, node, "status", &length);
        if (status && length == 5 && *status == 0x79616b6f)
            *status = 0x6574616c; /* okay -> late, same property size */
#endif
        return vendor_env_set("disp_reserve", (void *)0);
    }
    return vendor_reserve();
}
