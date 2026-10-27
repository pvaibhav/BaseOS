#ifndef BASEOS_BOOT_LOGO_PIXELS_H
#define BASEOS_BOOT_LOGO_PIXELS_H

#include <stdint.h>
#include <stddef.h>

static inline uint32_t baseos_logo_le32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* BMP orientation is physical; stock centers artwork on a zero-filled canvas. */
static inline int baseos_logo_pixels(const unsigned char *bmp, uint32_t bytes,
                                     uint32_t width, uint32_t height,
                                     uint32_t *pixels, size_t capacity)
{
    if (bytes < 54 || bmp[0] != 'B' || bmp[1] != 'M' ||
        baseos_logo_le32(bmp + 2) != bytes || !width || !height ||
        width > 4096 || height > 4096 || width > capacity / 4 / height) return 0;
    uint32_t dib = baseos_logo_le32(bmp + 14), offset = baseos_logo_le32(bmp + 10);
    uint32_t columns = baseos_logo_le32(bmp + 18);
    uint32_t stored_height = baseos_logo_le32(bmp + 22);
    int top_down = (stored_height >> 31) != 0;
    uint32_t rows = top_down ? 0u - stored_height : stored_height;
    if (dib < 40 || dib > bytes - 14 || offset < 14 + dib || offset > bytes ||
        !columns || columns > width || !rows || rows > height ||
        bmp[26] != 1 || bmp[27] || bmp[28] != 24 || bmp[29] ||
        baseos_logo_le32(bmp + 30)) return 0;
    uint32_t stride = (columns * 3 + 3) & ~3u;
    if (rows * stride > bytes - offset) return 0;
    uint32_t left = (width - columns) / 2, top = (height - rows) / 2;
    if (columns != width || rows != height)
        for (uint32_t i = 0; i < width * height; ++i) pixels[i] = 0;
    for (uint32_t y = 0; y < rows; y++) {
        const unsigned char *row = bmp + offset + (top_down ? y : rows - 1 - y) * stride;
        for (uint32_t x = 0; x < columns; x++) {
            const unsigned char *p = row + x * 3;
            pixels[(top + y) * width + left + x] = 0xff000000u | (uint32_t)p[2] << 16 | (uint32_t)p[1] << 8 | p[0];
        }
    }
    return 1;
}

#endif
