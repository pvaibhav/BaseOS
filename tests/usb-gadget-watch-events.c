#define main watcher_main
#include "../src/usb-gadget-watch.c"
#undef main
#include <assert.h>

static void status_fixture(const char *suffix, const char *value)
{
	char path[1024];
	snprintf(path, sizeof(path), "%s/%s", sys_root(), suffix);
	FILE *f = fopen(path, "w");
	assert(f);
	assert(fputs(value, f) >= 0);
	assert(fclose(f) == 0);
}

int main(void)
{
	const char disconnected[] = "change@/devices/virtual/android_usb/android0\0"
		"SUBSYSTEM=android_usb\0USB_STATE=DISCONNECTED\0";
	const char controller[] = "add@/devices/platform/soc/5100000.udc-controller\0SUBSYSTEM=udc\0";
	const char unrelated[] = "change@/devices/input/event0\0SUBSYSTEM=input\0";
	const char truncated[] = "SUBSYSTEM=android_usb";
	assert(usb_event(disconnected, sizeof(disconnected)));
	assert(usb_event(controller, sizeof(controller)));
	assert(!usb_event(unrelated, sizeof(unrelated)));
	assert(!usb_event(truncated, sizeof(truncated) - 1));
	assert(!event_has_field(disconnected, sizeof(disconnected), "SYSTEM=android_usb"));
	/* USB teardown can be announced before configfs has cleared the binding. */
	assert(recovery_event(disconnected, sizeof(disconnected)));
	const char power[] = "change@/devices/power_supply/axp2202-usb\0SUBSYSTEM=power_supply\0";
	status_fixture("kernel/config/usb_gadget/g1/UDC", "\n");
	status_fixture("devices/platform/soc/usbc0/otg_role", "usb_host\n");
	assert(!recovery_event(power, sizeof(power)));
	status_fixture("devices/platform/soc/usbc0/otg_role", "usb_device\n");
	assert(recovery_event(power, sizeof(power)));
	status_fixture("kernel/config/usb_gadget/g1/UDC", "5100000.udc-controller\n");
	assert(!recovery_event(power, sizeof(power)));
	/* A replug near the previous deadline must get a full settle window.
	 * Otherwise the UDC may become usable just after the old window ends. */
	long long unplug_deadline = SETTLE_MS;
	long long replug_time = SETTLE_MS - 1;
	assert(recovery_deadline(unplug_deadline, replug_time, true)
	       == replug_time + SETTLE_MS);
	/* A helper-generated CONFIGURED event cannot extend an active window. */
	assert(recovery_deadline(unplug_deadline, replug_time, false)
	       == unplug_deadline);
	assert(recovery_deadline(0, replug_time, false) == replug_time + SETTLE_MS);
	return 0;
}
