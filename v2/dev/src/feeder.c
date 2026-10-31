/* Dev watchdog feeder: 4 s hardware timeout, fed while the host keeps
 * touching /run/hb (at most GRACE s apart). On /run/stop or silence it runs
 * rcK (stop the frontend, unmount the card) while still feeding for up to
 * 10 s, then stops; NOWAYOUT resets the board and the loader falls back. */
#include <fcntl.h>
#include <linux/watchdog.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <unistd.h>
#define GRACE 60
int main(void) {
	int fd = open("/dev/watchdog", O_WRONLY | O_CLOEXEC), t = 4, left = GRACE;
	if (fd < 0) { perror("/dev/watchdog"); return 1; }
	if (ioctl(fd, WDIOC_SETTIMEOUT, &t)) perror("WDIOC_SETTIMEOUT");
	printf("feeder: timeout %d s, grace %d s\n", t, GRACE); fflush(stdout);
	while (left-- > 0 && access("/run/stop", F_OK)) {
		if (!unlink("/run/hb")) left = GRACE;
		ioctl(fd, WDIOC_KEEPALIVE, 0);
		sleep(1);
	}
	puts("feeder: stopping"); fflush(stdout);
	pid_t child = fork();
	if (child == 0) { execl("/bin/sh", "sh", "/etc/init.d/rcK", (char *)0); _exit(1); }
	for (int i = 0; i < 20 && waitpid(child, 0, WNOHANG) == 0; i++) {
		ioctl(fd, WDIOC_KEEPALIVE, 0);
		usleep(500000);
	}
	puts("feeder: stopped"); fflush(stdout);
	for (;;) pause(); /* keep the fd open: no magic close, reset follows */
}
