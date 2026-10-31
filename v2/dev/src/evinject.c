/* Presses keys on a real input device for remote testing: the events reach
 * every reader of that device as if the buttons had been pressed.
 *   evinject NAME MS CODE [CODE...]
 * NAME is the device name (e.g. ANBERNIC-keys, axp2202-pek); all CODEs are
 * pressed together, held for MS milliseconds and released. */
#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static void emit(int fd, int code, int value)
{
	struct input_event e[2] = { { .type = EV_KEY, .code = code, .value = value },
				    { .type = EV_SYN, .code = SYN_REPORT } };
	write(fd, e, sizeof(e));
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
		emit(fd, atoi(argv[i]), 1);
	usleep(atoi(argv[2]) * 1000);
	for (int i = 3; i < argc; i++)
		emit(fd, atoi(argv[i]), 0);
	return 0;
}
