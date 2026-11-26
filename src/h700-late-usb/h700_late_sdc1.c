// SPDX-License-Identifier: GPL-2.0
/* Start the H700 SDIO controller (sdc1) when held back as "late" in FDT.
 *
 * When Wi-Fi is unused during early boot (or disabled entirely on targets like
 * rg28xx), deferring sdc1 past kernel init avoids ~58 ms of pre-init MMC probe
 * and cmd52/5 RTO waits. On Wi-Fi targets, rcS loads this module immediately
 * before loading 8821cs.ko, mirroring h700_late_usb.ko.
 *
 * Uses exported device-tree and platform-bus calls only. Never unloaded because
 * replacing a read-only FDT property value transfers ownership of "okay".
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/string.h>
#include <linux/slab.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>

#define HELD_BACK "late"

static int __init h700_late_sdc1_init(void)
{
	struct device_node *node;

	for (node = of_find_compatible_node(NULL, NULL, "allwinner,sunxi-mmc-v4p1x");
	     node;
	     node = of_find_compatible_node(node, NULL, "allwinner,sunxi-mmc-v4p1x")) {
		struct device_node *bus;
		struct platform_device *bus_device, *created;
		struct property *dtype, *status;
		char *available;
		int length;

		dtype = of_find_property(node, "device_type", &length);
		if (!dtype || length != sizeof("sdc1") ||
		    memcmp(dtype->value, "sdc1", sizeof("sdc1")))
			continue;

		status = of_find_property(node, "status", &length);
		if (!status || length != sizeof(HELD_BACK) ||
		    memcmp(status->value, HELD_BACK, sizeof(HELD_BACK))) {
			of_node_put(node);
			return 0;
		}

		/* Boot-time FDT storage is read-only; kstrdup leaks intentionally
		 * for the lifetime of the live kernel tree. */
		available = kstrdup("okay", GFP_KERNEL);
		if (!available) {
			of_node_put(node);
			return -ENOMEM;
		}
		status->value = available;
		status->length = sizeof("okay");
		bus = of_get_parent(node);
		bus_device = bus ? of_find_device_by_node(bus) : NULL;
		created = of_platform_device_create(node, NULL,
						    bus_device ? &bus_device->dev : NULL);
		if (created)
			pr_info("h700_late_sdc1: started held-back sdc1 controller\n");
		else
			pr_err("h700_late_sdc1: could not create sdc1 platform device\n");
		if (bus_device)
			put_device(&bus_device->dev);
		of_node_put(bus);
		of_node_put(node);
		return 0;
	}
	return 0;
}
module_init(h700_late_sdc1_init);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Start H700 sdc1 SDIO controller held back by the boot device tree");
