/* baseos-charger: button checks for a start caused by plugging in a charger.
 *
 *   baseos-charger menu-held    exit 0 if MENU is held right now
 *   baseos-charger wait-power   block until POWER is pressed and held for one
 *                               second (a press already held at the start
 *                               must be released first); exit 0 then
 *
 * The policy (power off again, or wait) is in rcS, as in BaseOS 1.x
 * (docs/11-charger-only-boot.md). */
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define MENU 312        /* the pad's MENU button, as on stock */
#define HOLD_MS 1000

static int open_named(const char *want)
{
	for (int i = 0; i < 32; i++) {
		char path[32], name[64] = "";
		snprintf(path, sizeof(path), "/dev/input/event%d", i);
		int fd = open(path, O_RDONLY | O_CLOEXEC);
		if (fd < 0)
			continue;
		if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) >= 0 && !strcmp(name, want))
			return fd;
		close(fd);
	}
	return -1;
}

static int held(int fd, int code)
{
	unsigned char keys[KEY_MAX / 8 + 1] = {0};
	return ioctl(fd, EVIOCGKEY(sizeof(keys)), keys) >= 0 && (keys[code / 8] >> (code % 8) & 1);
}

/* Next POWER press (1) or release (0); -1 on error or, with a timeout, -2. */
static int next_power(int fd, int timeout_ms)
{
	struct input_event ev;
	struct pollfd p = { .fd = fd, .events = POLLIN };
	for (;;) {
		int n = poll(&p, 1, timeout_ms);
		if (n == 0)
			return -2;
		if (n < 0 || read(fd, &ev, sizeof(ev)) != sizeof(ev))
			return -1;
		if (ev.type == EV_KEY && ev.code == KEY_POWER && ev.value != 2)
			return ev.value;
	}
}

static int wait_power(void)
{
	int fd = open_named("axp2202-pek");
	if (fd < 0)
		return 1;
	if (held(fd, KEY_POWER))
		while (next_power(fd, -1) == 1)
			;
	for (;;) {
		int v = next_power(fd, -1);
		if (v < 0)
			return 1;
		if (v == 1 && next_power(fd, HOLD_MS) == -2)
			return 0;
	}
}

int main(int argc, char **argv)
{
	if (argc == 2 && !strcmp(argv[1], "menu-held")) {
		int fd = open_named("ANBERNIC-keys");
		return fd >= 0 && held(fd, MENU) ? 0 : 1;
	}
	if (argc == 2 && !strcmp(argv[1], "wait-power"))
		return wait_power();
	fprintf(stderr, "usage: baseos-charger menu-held|wait-power\n");
	return 2;
}
