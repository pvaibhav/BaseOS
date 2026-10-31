/* Presses keys on a real input device for remote testing: the events reach
 * every reader of that device as if the buttons had been pressed.
 *   evinject NAME MS CODE [CODE...]
 * NAME is the device name (e.g. ANBERNIC-keys, axp2202-pek); all CODEs are
 * pressed together, held for MS milliseconds and released. A CODE written as
 * hx-1, hx1, hy-1 or hy1 moves the d-pad hat (left, right, up, down). */
#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static void emit_type(int fd, int type, int code, int value)
{
	struct input_event e[2] = { { .type = type, .code = code, .value = value },
				    { .type = EV_SYN, .code = SYN_REPORT } };
	write(fd, e, sizeof(e));
}

/* "hx-1" -> ABS_HAT0X -1, "hy1" -> ABS_HAT0Y 1; anything else is a key code. */
static void press(int fd, const char *arg, int down)
{
	if (arg[0] == 'h' && (arg[1] == 'x' || arg[1] == 'y'))
		emit_type(fd, EV_ABS, arg[1] == 'x' ? ABS_HAT0X : ABS_HAT0Y, down ? atoi(arg + 2) : 0);
	else
		emit_type(fd, EV_KEY, atoi(arg), down);
}

int main(int argc, char **argv)
{
	if (argc < 4) {
		fprintf(stderr, "usage: evinject NAME MS CODE [CODE...]\n");
		return 2;
	}
	int fd = -1;
	for (int i = 0; i < 32 && fd < 0; i++) {
		char path[32], name[64] = "";
		snprintf(path, sizeof(path), "/dev/input/event%d", i);
		fd = open(path, O_WRONLY);
		if (fd >= 0 && (ioctl(fd, EVIOCGNAME(sizeof(name)), name) < 0 || strcmp(name, argv[1]))) {
			close(fd);
			fd = -1;
		}
	}
	if (fd < 0) {
		fprintf(stderr, "evinject: no device named %s\n", argv[1]);
		return 1;
	}
	for (int i = 3; i < argc; i++)
		press(fd, argv[i], 1);
	usleep(atoi(argv[2]) * 1000);
	for (int i = 3; i < argc; i++)
		press(fd, argv[i], 0);
	return 0;
}
