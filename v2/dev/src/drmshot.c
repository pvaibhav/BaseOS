/* drmshot: save what the display is showing right now (BaseOS 2 has no
 * /dev/fb0). Reads the framebuffer on the first active CRTC through
 * GETFB2 + PRIME and writes a binary PPM.
 *   drmshot OUT.ppm
 * Only linear 32-bit XRGB/ARGB framebuffers are supported (what Mesa and
 * the boot logo use for scanout on H700); anything else is reported. */
#include <drm.h>
#include <drm_fourcc.h>
#include <drm_mode.h>
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: drmshot OUT.ppm\n");
		return 2;
	}
	for (int c = 0; c < 4; c++) {
		char path[32];
		snprintf(path, sizeof(path), "/dev/dri/card%d", c);
		int fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		uint32_t crtcs[8];
		struct drm_mode_card_res res = { .crtc_id_ptr = (uintptr_t)crtcs, .count_crtcs = 8 };
		if (ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res) || !res.count_crtcs) {
			close(fd);
			continue;
		}
		for (uint32_t i = 0; i < res.count_crtcs && i < 8; i++) {
			struct drm_mode_crtc crtc = { .crtc_id = crtcs[i] };
			if (ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &crtc) || !crtc.fb_id)
				continue;
			struct drm_mode_fb_cmd2 fb = { .fb_id = crtc.fb_id };
			if (ioctl(fd, DRM_IOCTL_MODE_GETFB2, &fb) || !fb.handles[0]) {
				perror("GETFB2 (needs root)");
				return 1;
			}
			if ((fb.pixel_format != DRM_FORMAT_XRGB8888 && fb.pixel_format != DRM_FORMAT_ARGB8888) ||
			    ((fb.flags & DRM_MODE_FB_MODIFIERS) && fb.modifier[0] != DRM_FORMAT_MOD_LINEAR)) {
				fprintf(stderr, "unsupported framebuffer: format %.4s modifier 0x%llx\n",
					(char *)&fb.pixel_format, (unsigned long long)fb.modifier[0]);
				return 1;
			}
			struct drm_prime_handle prime = { .handle = fb.handles[0], .flags = DRM_CLOEXEC };
			if (ioctl(fd, DRM_IOCTL_PRIME_HANDLE_TO_FD, &prime)) {
				perror("PRIME_HANDLE_TO_FD");
				return 1;
			}
			size_t size = (size_t)fb.pitches[0] * fb.height + fb.offsets[0];
			uint8_t *map = mmap(0, size, PROT_READ, MAP_SHARED, prime.fd, 0);
			if (map == MAP_FAILED) {
				perror("mmap");
				return 1;
			}
			struct dma_buf_sync sync = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ };
			ioctl(prime.fd, DMA_BUF_IOCTL_SYNC, &sync);
			FILE *out = fopen(argv[1], "wb");
			if (!out) {
				perror(argv[1]);
				return 1;
			}
			fprintf(out, "P6\n%u %u\n255\n", fb.width, fb.height);
			uint8_t *row = malloc(fb.width * 3);
			for (uint32_t y = 0; y < fb.height; y++) {
				const uint32_t *px = (const uint32_t *)(map + fb.offsets[0] + (size_t)y * fb.pitches[0]);
				for (uint32_t x = 0; x < fb.width; x++) {
					row[x * 3] = px[x] >> 16;
					row[x * 3 + 1] = px[x] >> 8;
					row[x * 3 + 2] = px[x];
				}
				fwrite(row, 3, fb.width, out);
			}
			sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
			ioctl(prime.fd, DMA_BUF_IOCTL_SYNC, &sync);
			fclose(out);
			printf("%ux%u from %s crtc %u fb %u\n", fb.width, fb.height, path, crtcs[i], fb.fb_id);
			return 0;
		}
		close(fd);
	}
	fprintf(stderr, "no active framebuffer found\n");
	return 1;
}
