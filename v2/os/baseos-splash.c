/*
 * baseos-splash: show the boot logo from the moment userspace starts until the
 * frontend draws its first frame, so the screen does not go blank in between.
 *
 * Draws /usr/share/baseos/bootlogo.bmp (24-bit, panel-native size: the same file
 * the boot loader shows) on the panel, then gives up DRM master at once so the
 * frontend becomes master as usual. Exits when another framebuffer replaces the
 * logo, or after 20 seconds.
 */
#include <drm.h>
#include <drm_mode.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define LOGO "/usr/share/baseos/bootlogo.bmp"

static int find_card(void)
{
	for (int i = 0; i < 4; i++) {
		char path[32];
		snprintf(path, sizeof(path), "/dev/dri/card%d", i);
		int fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		struct drm_mode_card_res res = {0};
		if (!ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res) && res.count_connectors && res.count_crtcs)
			return fd;
		close(fd);
	}
	return -1;
}

int main(void)
{
	int fd = find_card();
	if (fd < 0)
		return 1;

	uint32_t conns[4], crtcs[4], encs[4];
	struct drm_mode_card_res res = {
		.connector_id_ptr = (uintptr_t)conns, .count_connectors = 4,
		.crtc_id_ptr = (uintptr_t)crtcs, .count_crtcs = 4,
		.encoder_id_ptr = (uintptr_t)encs, .count_encoders = 4,
	};
	if (ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res))
		return 1;

	/* Asking with no room for modes makes the kernel probe the connector. */
	struct drm_mode_modeinfo modes[8], mode;
	struct drm_mode_get_connector conn = { .connector_id = conns[0] };
	if (ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) || !conn.count_modes)
		return 1;
	conn = (struct drm_mode_get_connector){ .connector_id = conns[0], .count_modes = 8,
						.modes_ptr = (uintptr_t)modes };
	if (ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) || !conn.count_modes)
		return 1;
	mode = modes[0];
	uint32_t w = mode.hdisplay, h = mode.vdisplay;

	/* BMP: 24-bit, bottom-up rows padded to 4 bytes, panel-native size. */
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
				      .count_connectors = 1, .mode = mode, .mode_valid = 1 };
	if (ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc))
		return 1;
	ioctl(fd, DRM_IOCTL_DROP_MASTER, 0);

	/* Keep the logo's framebuffer alive until the frontend replaces it, and
	 * note when that happened (seconds since power-on, 24 MHz counter). */
	for (int i = 0; i < 1000; i++) {
		struct drm_mode_crtc now = { .crtc_id = crtcs[0] };
		if (!ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &now) && now.fb_id != cmd.fb_id) {
			uint64_t c;
			asm volatile("mrs %0, cntvct_el0" : "=r"(c));
			FILE *t = fopen("/run/baseos/t-frontend-frame", "w");
			if (t) { fprintf(t, "%.3f\n", c / 24e6); fclose(t); }
			break;
		}
		usleep(20000);
	}
	return 0;
}
