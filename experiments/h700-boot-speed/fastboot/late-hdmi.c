// SPDX-License-Identifier: GPL-2.0
/* Lab: start a held-back HDMI device through the normal platform driver. */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/string.h>
#include <linux/slab.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>

#define HELD_BACK "late"

static const char *const controllers[] = { "allwinner,sunxi-hdmi" };

static int __init h700_late_hdmi_init(void)
{
	unsigned int i, started = 0, failed = 0;

	for (i = 0; i < ARRAY_SIZE(controllers); i++) {
		struct device_node *node, *bus;
		struct platform_device *bus_device, *created;
		struct property *status;
		char *available;
		int length;

		node = of_find_compatible_node(NULL, NULL, controllers[i]);
		if (!node)
			continue;
		status = of_find_property(node, "status", &length);
		if (!status || length != sizeof(HELD_BACK) ||
		    memcmp(status->value, HELD_BACK, sizeof(HELD_BACK))) {
			of_node_put(node);
			continue;
		}
		/* The platform bus refuses a node that is not available. Property
		 * values still point into the boot-time device-tree blob, which is
		 * mapped read-only, so give the property a value of its own. It is
		 * never freed: the node keeps it for as long as the system runs.
		 */
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
		if (created) {
			started++;
		} else {
			failed++;
			pr_err("h700_late_hdmi: could not create %s\n", controllers[i]);
		}
		if (bus_device)
			put_device(&bus_device->dev);
		of_node_put(bus);
		of_node_put(node);
	}
	pr_info("h700_late_hdmi: started %u held-back HDMI devices, %u failed\n",
		started, failed);
	return 0;
}
module_init(h700_late_hdmi_init);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Start H700 USB HDMI devices held back by the boot device tree");
