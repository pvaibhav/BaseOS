/* BaseOS 2 first stage (the whole initramfs): loop-mount the read-only root
 * image stored at a fixed offset on the card and hand over to its init.
 * On failure it logs to the kernel and stops; the watchdog then restarts the
 * board and the loader falls back to the previous system. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/loop.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

#define DISK "/dev/mmcblk0"
#define ROOT_OFFSET (434176ULL * 512) /* card slot A */

static void fail(const char *what)
{
	int k = open("/dev/kmsg", O_WRONLY);
	if (k >= 0) dprintf(k, "baseos firststage: %s: %s\n", what, strerror(errno));
	for (;;) pause();
}

static int open_retry(const char *path, int flags)
{
	for (int i = 0; i < 500; i++) { /* SD probing is asynchronous: wait up to 5 s */
		int fd = open(path, flags | O_CLOEXEC);
		if (fd >= 0) return fd;
		usleep(10000);
	}
	return -1;
}

int main(void)
{
	char loop[32];
	mount("devtmpfs", "/dev", "devtmpfs", 0, 0);
	int disk = open_retry(DISK, O_RDONLY);
	if (disk < 0) fail("card");
	int control = open_retry("/dev/loop-control", O_RDWR);
	int n = control < 0 ? -1 : ioctl(control, LOOP_CTL_GET_FREE);
	if (n < 0) fail("loop-control");
	snprintf(loop, sizeof(loop), "/dev/loop%d", n);
	int fd = open_retry(loop, O_RDONLY);
	struct loop_config config = { .fd = disk };
	config.info.lo_offset = ROOT_OFFSET;
	config.info.lo_flags = LO_FLAGS_READ_ONLY;
	if (fd < 0 || ioctl(fd, LOOP_CONFIGURE, &config)) fail("loop");
	mkdir("/newroot", 0755);
	if (mount(loop, "/newroot", "squashfs", MS_RDONLY, 0)) fail("root mount");
	if (mount("/dev", "/newroot/dev", 0, MS_MOVE, 0) || chdir("/newroot") ||
	    mount(".", "/", 0, MS_MOVE, 0) || chroot(".") || chdir("/")) fail("switch");
	execl("/usr/bin/busybox", "init", (char *)0);
	fail("exec init");
}
