/* Virtual copy of the RG28XX pad ("ANBERNIC-keys") for remote testing.
 * Reads commands from the FIFO /run/pad, one per line:
 *   up|down|left|right|a|b|x|y|start|select|l1|l2|r1|r2|menu|vol+|vol- [ms]
 * A button is held for ms (default 120) and released. */
#include <fcntl.h>
#include <linux/uinput.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int fd;
static void emit(int type, int code, int value) {
	struct input_event e = { .type = type, .code = code, .value = value };
	write(fd, &e, sizeof(e));
	struct input_event s = { .type = EV_SYN, .code = SYN_REPORT };
	write(fd, &s, sizeof(s));
}
static const struct { const char *n; int type, code, value; } map[] = {
	{"up", EV_ABS, ABS_HAT0Y, -1}, {"down", EV_ABS, ABS_HAT0Y, 1},
	{"left", EV_ABS, ABS_HAT0X, -1}, {"right", EV_ABS, ABS_HAT0X, 1},
	{"a", EV_KEY, 0x130}, {"b", EV_KEY, 0x131}, {"x", EV_KEY, 0x132}, {"y", EV_KEY, 0x133},
	{"l1", EV_KEY, 0x134}, {"r1", EV_KEY, 0x135}, {"select", EV_KEY, 0x136},
	{"start", EV_KEY, 0x137}, {"menu", EV_KEY, 0x138}, {"l2", EV_KEY, 0x13a},
	{"r2", EV_KEY, 0x13b}, {"vol+", EV_KEY, KEY_VOLUMEUP}, {"vol-", EV_KEY, KEY_VOLUMEDOWN},
};
int main(void) {
	fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
	if (fd < 0) { perror("/dev/uinput"); return 1; }
	ioctl(fd, UI_SET_EVBIT, EV_KEY); ioctl(fd, UI_SET_EVBIT, EV_ABS);
	for (unsigned i = 0; i < sizeof(map) / sizeof(*map); i++)
		if (map[i].type == EV_KEY) ioctl(fd, UI_SET_KEYBIT, map[i].code);
	struct uinput_abs_setup abs = { .absinfo = { .minimum = -1, .maximum = 1 } };
	abs.code = ABS_HAT0X; ioctl(fd, UI_SET_ABSBIT, ABS_HAT0X); ioctl(fd, UI_ABS_SETUP, &abs);
	abs.code = ABS_HAT0Y; ioctl(fd, UI_SET_ABSBIT, ABS_HAT0Y); ioctl(fd, UI_ABS_SETUP, &abs);
	struct uinput_setup setup = { .id = { BUS_HOST, 1, 1, 0x100 } };
	strcpy(setup.name, "ANBERNIC-keys");
	if (ioctl(fd, UI_DEV_SETUP, &setup) || ioctl(fd, UI_DEV_CREATE)) { perror("uinput"); return 1; }
	mkfifo("/run/pad", 0600);
	char line[64];
	for (;;) {
		FILE *f = fopen("/run/pad", "r");
		if (!f) return 1;
		while (fgets(line, sizeof(line), f)) {
			char name[16]; int ms = 120;
			if (sscanf(line, "%15s %d", name, &ms) < 1) continue;
			for (unsigned i = 0; i < sizeof(map) / sizeof(*map); i++) {
				if (strcmp(name, map[i].n)) continue;
				emit(map[i].type, map[i].code, map[i].type == EV_ABS ? map[i].value : 1);
				usleep(ms * 1000);
				emit(map[i].type, map[i].code, 0);
				usleep(80000);
			}
		}
		fclose(f);
	}
}
