// SPDX-License-Identifier: GPL-2.0
/*
 * BaseOS board overlay: device tree nodes the boot firmware refuses to see
 * (it rejects any x-powers PMIC node), applied before devices are created.
 */
#include <linux/init.h>
#include <linux/of.h>
#include <linux/printk.h>

extern u8 __dtbo_baseos_rg28xx_begin[], __dtbo_baseos_rg28xx_end[];

static int __init baseos_overlay_init(void)
{
	int id = 0, ret;

	if (!of_machine_is_compatible("anbernic,rg28xx"))
		return 0;
	ret = of_overlay_fdt_apply(__dtbo_baseos_rg28xx_begin,
				   __dtbo_baseos_rg28xx_end - __dtbo_baseos_rg28xx_begin,
				   &id, NULL);
	pr_info("baseos: board overlay %s (%d)\n", ret ? "failed" : "applied", ret);
	return 0;
}
arch_initcall(baseos_overlay_init);
