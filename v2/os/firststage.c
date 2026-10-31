/* BaseOS 2 first stage (the whole initramfs).
 *
 * 1. Puts the boot logo back on the screen at once (the display driver clears
 *    it when it starts) and keeps it there until the frontend draws its first
 *    frame.
 * 2. Loop-mounts the read-only root image stored at a fixed offset on the card
 *    and hands over to its init.
 *
 * On failure it logs to the kernel and stops; the watchdog then restarts the
 * board and the loader falls back to the previous system. */
#define _GNU_SOURCE
#include <drm.h>
#include <drm_mode.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/loop.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

#define DISK "/dev/mmcblk0"
/* The two 512 MiB system slots. BaseOS 2 lives in whichever one holds a
 * squashfs image; BaseOS 1.x (ext4) uses the other. */
static const unsigned long long slots[] = { 434176ULL * 512, 1482752ULL * 512 };
#define LOGO "/logo.bmp"              /* 24-bit, panel-native size, as the loader shows */

static int kmsg = -1;

static void fail(const char *what)
{
	dprintf(kmsg, "baseos firststage: %s: %s\n", what, strerror(errno));
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

static double since_power_on(void)
{
	uint64_t c;
	asm volatile("mrs %0, cntvct_el0" : "=r"(c));
	return c / 24e6;
}

/* Draws the logo, gives up DRM master so the frontend can take the screen, and
 * keeps the logo's framebuffer alive until another one replaces it (or 20 s). */
static int splash(void)
{
	int fd = -1;
	uint32_t conns[4], crtcs[4], encs[4];
	struct drm_mode_card_res res;
	for (int i = 0; i < 4 && fd < 0; i++) {
		char path[32];
		snprintf(path, sizeof(path), "/dev/dri/card%d", i);
		fd = open(path, O_RDWR | O_CLOEXEC);
		res = (struct drm_mode_card_res){
			.connector_id_ptr = (uintptr_t)conns, .count_connectors = 4,
			.crtc_id_ptr = (uintptr_t)crtcs, .count_crtcs = 4,
			.encoder_id_ptr = (uintptr_t)encs, .count_encoders = 4,
		};
		if (fd >= 0 && (ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res) || !res.count_connectors || !res.count_crtcs)) {
			close(fd);
			fd = -1;
		}
	}
	if (fd < 0)
		return 1;

	/* Asking with no room for modes makes the kernel probe the connector. */
	struct drm_mode_modeinfo modes[8];
	struct drm_mode_get_connector conn = { .connector_id = conns[0] };
	if (ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) || !conn.count_modes)
		return 1;
	conn = (struct drm_mode_get_connector){ .connector_id = conns[0], .count_modes = 8,
						.modes_ptr = (uintptr_t)modes };
	if (ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) || !conn.count_modes)
		return 1;
	uint32_t w = modes[0].hdisplay, h = modes[0].vdisplay;

	/* BMP: 24-bit, bottom-up rows padded to 4 bytes. */
	FILE *f = fopen(LOGO, "rb");
	unsigned char hdr[54];
	if (!f || fread(hdr, 1, 54, f) != 54 || hdr[0] != 'B' || hdr[1] != 'M' ||
	    *(int32_t *)(hdr + 18) != (int32_t)w || *(int32_t *)(hdr + 22) != (int32_t)h ||
	    *(uint16_t *)(hdr + 28) != 24)
		return 1;
	fseek(f, *(uint32_t *)(hdr + 10), SEEK_SET);

	struct drm_mode_create_dumb dumb = { .width = w, .height = h, .bpp = 32 };
	if (ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &dumb))
		return 1;
	struct drm_mode_map_dumb map = { .handle = dumb.handle };
	if (ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map))
		return 1;
	uint8_t *fb = mmap(0, dumb.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, map.offset);
	if (fb == MAP_FAILED)
		return 1;
	unsigned char row[4096 * 3];
	size_t stride = (w * 3 + 3) & ~3u;
	for (uint32_t y = 0; y < h; y++) {
		if (fread(row, 1, stride, f) != stride)
			return 1;
		uint32_t *out = (uint32_t *)(fb + (h - 1 - y) * dumb.pitch);
		for (uint32_t x = 0; x < w; x++)
			out[x] = row[x * 3] | row[x * 3 + 1] << 8 | row[x * 3 + 2] << 16;
	}
	fclose(f);

	struct drm_mode_fb_cmd cmd = { .width = w, .height = h, .pitch = dumb.pitch,
				       .bpp = 32, .depth = 24, .handle = dumb.handle };
	if (ioctl(fd, DRM_IOCTL_MODE_ADDFB, &cmd))
		return 1;
	struct drm_mode_crtc crtc = { .crtc_id = crtcs[0], .fb_id = cmd.fb_id,
				      .set_connectors_ptr = (uintptr_t)&conns[0],
				      .count_connectors = 1, .mode = modes[0], .mode_valid = 1 };
	if (ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc))
		return 1;
	ioctl(fd, DRM_IOCTL_DROP_MASTER, 0);
	dprintf(kmsg, "baseos-splash: logo on screen\n");

	for (int i = 0; i < 1000; i++) {
		struct drm_mode_crtc now = { .crtc_id = crtcs[0] };
		if (!ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &now) && now.fb_id != cmd.fb_id) {
			dprintf(kmsg, "baseos-splash: frontend frame at %.3f s after power-on\n", since_power_on());
			break;
		}
		usleep(20000);
	}
	return 0;
}

int main(void)
{
	char loop[32];
	mount("devtmpfs", "/dev", "devtmpfs", 0, 0);
	kmsg = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
	if (fork() == 0)
		_exit(splash());
	int disk = open_retry(DISK, O_RDONLY);
	if (disk < 0) fail("card");
	int control = open_retry("/dev/loop-control", O_RDWR);
	int n = control < 0 ? -1 : ioctl(control, LOOP_CTL_GET_FREE);
	if (n < 0) fail("loop-control");
	snprintf(loop, sizeof(loop), "/dev/loop%d", n);
	int fd = open_retry(loop, O_RDONLY);
	struct loop_config config = { .fd = disk };
	for (unsigned i = 0; i < 2 && !config.info.lo_offset; i++) {
		char magic[4];
		if (pread(disk, magic, 4, slots[i]) == 4 && !memcmp(magic, "hsqs", 4))
			config.info.lo_offset = slots[i];
	}
	if (!config.info.lo_offset) {
		errno = ENOENT;
		fail("no BaseOS 2 system slot");
	}
	config.info.lo_flags = LO_FLAGS_READ_ONLY;
	if (fd < 0 || ioctl(fd, LOOP_CONFIGURE, &config)) fail("loop");
	mkdir("/newroot", 0755);
	if (mount(loop, "/newroot", "squashfs", MS_RDONLY, 0)) fail("root mount");
	if (mount("/dev", "/newroot/dev", 0, MS_MOVE, 0) || chdir("/newroot") ||
	    mount(".", "/", 0, MS_MOVE, 0) || chroot(".") || chdir("/")) fail("switch");
	execl("/usr/bin/busybox", "init", (char *)0);
	fail("exec init");
}
