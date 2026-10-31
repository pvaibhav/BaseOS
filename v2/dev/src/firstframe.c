/* Print seconds since power-on (24 MHz arch counter) at which the panel's DRM
 * connector is first enabled, i.e. a frontend set its first mode and frame.
 * Reads sysfs only: holding the DRM device open could steal master. */
#include <fcntl.h>
#include <glob.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
static double now(void) { uint64_t c; asm volatile("mrs %0, cntvct_el0" : "=r"(c)); return c / 24e6; }
int main(void) {
	for (int i = 0; i < 10000; i++) {
		glob_t g;
		if (!glob("/sys/class/drm/card*-*/enabled", 0, 0, &g)) {
			for (size_t k = 0; k < g.gl_pathc; k++) {
				char buf[16] = {0};
				int fd = open(g.gl_pathv[k], O_RDONLY);
				if (fd >= 0) { read(fd, buf, sizeof(buf) - 1); close(fd); }
				if (!strncmp(buf, "enabled", 7)) { printf("%.3f\n", now()); return 0; }
			}
			globfree(&g);
		}
		usleep(2000);
	}
	return 1;
}
