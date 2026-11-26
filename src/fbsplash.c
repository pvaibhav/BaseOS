// Minimal framebuffer boot splash/status renderer for the H700 base OS.
//
//   fbsplash <progress 0-100>              full-screen static logo
//   fbsplash <progress 0-100|-1> <message> compact status pill overlay
//
// The mode follows the message, not a flag: with a message the renderer
// overlays a compact status surface and preserves every pixel outside it; with
// none it draws the full-screen logo used by the bootloader image and by the
// charger wait when POWER is accepted. Other runtime calls pass a message.
// -1 suppresses the pill's progress track (an action or error state).
//
// No options are accepted, and anything option-shaped is a hard error — see
// reject_options(). Text is crisp anti-aliased Lexend via freetype; if the font
// is missing it falls back to a built-in bitmap so boot never blocks.
//
// Panels that are mounted turned relative to how the device is held are handled
// by present(), which rotates a finished render onto the framebuffer. Every
// drawing routine below works in upright, as-held coordinates and knows nothing
// about orientation.
#include <ctype.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <ft2build.h>
#include FT_FREETYPE_H

#define FONT_PATH "/usr/share/baseos/boot.ttf"

typedef struct {
	uint8_t rows[7];
} Glyph;

typedef struct {
	char c;
	Glyph g;
} GlyphMap;

// 5x7 bitmap font — fallback only (used when the TTF is unavailable).
static const GlyphMap glyphs[] = {
	{' ', {{0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}}},
	{'.', {{0x00, 0x00, 0x00, 0x00, 0x00, 0x0c, 0x0c}}},
	{'0', {{0x0e, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0e}}},
	{'1', {{0x04, 0x0c, 0x04, 0x04, 0x04, 0x04, 0x0e}}},
	{'A', {{0x0e, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11}}},
	{'B', {{0x1e, 0x11, 0x11, 0x1e, 0x11, 0x11, 0x1e}}},
	{'C', {{0x0e, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0e}}},
	{'D', {{0x1e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1e}}},
	{'E', {{0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x1f}}},
	{'F', {{0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x10}}},
	{'G', {{0x0e, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0f}}},
	{'H', {{0x11, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11}}},
	{'I', {{0x0e, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0e}}},
	{'J', {{0x07, 0x02, 0x02, 0x02, 0x12, 0x12, 0x0c}}},
	{'K', {{0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11}}},
	{'L', {{0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1f}}},
	{'M', {{0x11, 0x1b, 0x15, 0x15, 0x11, 0x11, 0x11}}},
	{'N', {{0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11}}},
	{'O', {{0x0e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e}}},
	{'P', {{0x1e, 0x11, 0x11, 0x1e, 0x10, 0x10, 0x10}}},
	{'Q', {{0x0e, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0d}}},
	{'R', {{0x1e, 0x11, 0x11, 0x1e, 0x14, 0x12, 0x11}}},
	{'S', {{0x0f, 0x10, 0x10, 0x0e, 0x01, 0x01, 0x1e}}},
	{'T', {{0x1f, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}}},
	{'U', {{0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e}}},
	{'V', {{0x11, 0x11, 0x11, 0x11, 0x11, 0x0a, 0x04}}},
	{'W', {{0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0a}}},
	{'X', {{0x11, 0x11, 0x0a, 0x04, 0x0a, 0x11, 0x11}}},
	{'Y', {{0x11, 0x11, 0x0a, 0x04, 0x04, 0x04, 0x04}}},
	{'Z', {{0x1f, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1f}}},
};

static const Glyph *glyph_for(char c) {
	c = (char)toupper((unsigned char)c);
	for (size_t i = 0; i < sizeof(glyphs) / sizeof(glyphs[0]); i++) {
		if (glyphs[i].c == c)
			return &glyphs[i].g;
	}
	return &glyphs[0].g;
}

static uint32_t make_pixel(struct fb_var_screeninfo *v, uint8_t r, uint8_t g, uint8_t b) {
	uint32_t pixel = 0;
	pixel |= ((uint32_t)(r >> (8 - v->red.length))) << v->red.offset;
	pixel |= ((uint32_t)(g >> (8 - v->green.length))) << v->green.offset;
	pixel |= ((uint32_t)(b >> (8 - v->blue.length))) << v->blue.offset;
	return pixel;
}

static void put_pixel(uint8_t *fb, struct fb_var_screeninfo *v, struct fb_fix_screeninfo *f, int x, int y, uint32_t pixel) {
	if (x < 0 || y < 0 || x >= (int)v->xres || y >= (int)v->yres)
		return;
	uint8_t *dst = fb + y * f->line_length + x * (v->bits_per_pixel / 8);
	switch (v->bits_per_pixel) {
		case 16: *(uint16_t *)dst = (uint16_t)pixel; break;
		case 24: dst[0] = pixel & 0xff; dst[1] = (pixel >> 8) & 0xff; dst[2] = (pixel >> 16) & 0xff; break;
		case 32: *(uint32_t *)dst = pixel; break;
	}
}

// Copy one rectangle between two buffers that share the framebuffer's stride.
static void blit_rect(uint8_t *dst, const uint8_t *src, struct fb_var_screeninfo *v,
                      struct fb_fix_screeninfo *f, int x, int y, int w, int h) {
	int bytes = v->bits_per_pixel / 8;
	if (x < 0) { w += x; x = 0; }
	if (y < 0) { h += y; y = 0; }
	if (x + w > (int)v->xres) w = (int)v->xres - x;
	if (y + h > (int)v->yres) h = (int)v->yres - y;
	for (int i = 0; i < h; i++) {
		size_t off = (size_t)(y + i) * f->line_length + (size_t)x * bytes;
		memcpy(dst + off, src + off, (size_t)w * bytes);
	}
}

static uint32_t get_pixel(uint8_t *fb, struct fb_var_screeninfo *v,
                          struct fb_fix_screeninfo *f, int x, int y) {
	uint8_t *src = fb + y * f->line_length + x * (v->bits_per_pixel / 8);
	switch (v->bits_per_pixel) {
		case 16: return *(uint16_t *)src;
		case 24: return (uint32_t)src[0] | ((uint32_t)src[1] << 8) | ((uint32_t)src[2] << 16);
		case 32: return *(uint32_t *)src;
		default: return 0;
	}
}

static uint8_t pixel_channel(uint32_t pixel, struct fb_bitfield field) {
	if (!field.length)
		return 0;
	uint32_t mask = (1u << field.length) - 1u;
	uint32_t value = (pixel >> field.offset) & mask;
	return (uint8_t)((value * 255u + mask / 2u) / mask);
}

static void blend_pixel(uint8_t *fb, struct fb_var_screeninfo *v,
                        struct fb_fix_screeninfo *f, int x, int y,
                        int r, int g, int b, int alpha) {
	if (x < 0 || y < 0 || x >= (int)v->xres || y >= (int)v->yres || alpha <= 0)
		return;
	uint32_t under = get_pixel(fb, v, f, x, y);
	int ur = pixel_channel(under, v->red);
	int ug = pixel_channel(under, v->green);
	int ub = pixel_channel(under, v->blue);
	r = (r * alpha + ur * (255 - alpha)) / 255;
	g = (g * alpha + ug * (255 - alpha)) / 255;
	b = (b * alpha + ub * (255 - alpha)) / 255;
	put_pixel(fb, v, f, x, y, make_pixel(v, r, g, b));
}

static int inside_round_rect(double px, double py, int x, int y, int w, int h, int radius) {
	double cx = px < x + radius ? x + radius :
	            px > x + w - radius ? x + w - radius : px;
	double cy = py < y + radius ? y + radius :
	            py > y + h - radius ? y + h - radius : py;
	double dx = px - cx, dy = py - cy;
	return dx * dx + dy * dy <= (double)radius * radius;
}

static void round_rect(uint8_t *fb, struct fb_var_screeninfo *v,
                       struct fb_fix_screeninfo *f, int x, int y, int w, int h,
                       int radius, const int colour[3], int opacity) {
	for (int py = y; py < y + h; py++) {
		for (int px = x; px < x + w; px++) {
			int covered = 0;
			for (int sy = 0; sy < 2; sy++)
				for (int sx = 0; sx < 2; sx++)
					covered += inside_round_rect(
						px + (sx + 0.5) / 2.0,
						py + (sy + 0.5) / 2.0,
						x, y, w, h, radius);
			if (covered)
				blend_pixel(fb, v, f, px, py, colour[0], colour[1], colour[2],
				            opacity * covered / 4);
		}
	}
}

// Subtle vertical ground gradient (top slightly lighter than bottom).
static void bg_rgb(int y, int yres, uint8_t *r, uint8_t *g, uint8_t *b) {
	double t = yres > 1 ? (double)y / (yres - 1) : 0.0;
	*r = (uint8_t)(0x0C + t * (0x09 - 0x0C));
	*g = (uint8_t)(0x0F + t * (0x0B - 0x0F));
	*b = (uint8_t)(0x12 + t * (0x0D - 0x12));
}

static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

static double segment_dist2(double px, double py, double ax, double ay, double bx, double by) {
	double dx = bx - ax, dy = by - ay;
	double denom = dx * dx + dy * dy;
	double t = denom > 0 ? ((px - ax) * dx + (py - ay) * dy) / denom : 0;
	if (t < 0) t = 0;
	if (t > 1) t = 1;
	double ex = px - (ax + t * dx), ey = py - (ay + t * dy);
	return ex * ex + ey * ey;
}

// The bundled Lexend font does not contain U+2713. Draw that one status glyph
// ourselves so the frontend-ready state remains crisp and font-independent.
static void draw_check(uint8_t *fb, struct fb_var_screeninfo *v, struct fb_fix_screeninfo *f,
                       const int colour[3]) {
	int H = (int)v->yres, W = (int)v->xres;
	int size = clampi((int)(H * 0.065), 24, 80);
	double cx = W * 0.5, cy = H * 0.625;
	double ax = cx - size * 0.55, ay = cy - size * 0.02;
	double bx = cx - size * 0.13, by = cy + size * 0.38;
	double dx = cx + size * 0.62, dy = cy - size * 0.40;
	double radius = clampi((int)(size * 0.105), 3, 10);
	int minx = (int)(cx - size * 0.72), maxx = (int)(cx + size * 0.78);
	int miny = (int)(cy - size * 0.58), maxy = (int)(cy + size * 0.56);
	double r2 = radius * radius;

	// 3x3 supersampling keeps the diagonals smooth without another dependency.
	for (int y = miny; y <= maxy; y++) {
		for (int x = minx; x <= maxx; x++) {
			int covered = 0;
			for (int sy = 0; sy < 3; sy++) {
				for (int sx = 0; sx < 3; sx++) {
					double px = x + (sx + 0.5) / 3.0;
					double py = y + (sy + 0.5) / 3.0;
					if (segment_dist2(px, py, ax, ay, bx, by) <= r2 ||
					    segment_dist2(px, py, bx, by, dx, dy) <= r2)
						covered++;
				}
			}
			if (!covered)
				continue;
			int alpha = covered * 255 / 9;
			uint8_t br, bg, bb;
			bg_rgb(y, H, &br, &bg, &bb);
			int r = (colour[0] * alpha + br * (255 - alpha)) / 255;
			int g = (colour[1] * alpha + bg * (255 - alpha)) / 255;
			int b = (colour[2] * alpha + bb * (255 - alpha)) / 255;
			put_pixel(fb, v, f, x, y, make_pixel(v, r, g, b));
		}
	}
}

// --- freetype path ---------------------------------------------------------

static int ft_measure(FT_Face face, const char *s, int tracking) {
	int w = 0;
	for (; *s; s++) {
		if (FT_Load_Char(face, (unsigned char)*s, FT_LOAD_DEFAULT))
			continue;
		w += face->glyph->advance.x >> 6;
		if (s[1])
			w += tracking;
	}
	return w;
}

// Draw a line of text. Each pixel's colour is interpolated between dim and
// bright by its position relative to reveal_x (a soft ramp), giving the
// left-to-right illumination; pass reveal_x >= right edge for solid bright.
static void ft_draw(uint8_t *fb, struct fb_var_screeninfo *v, struct fb_fix_screeninfo *f,
                    FT_Face face, const char *s, int x, int baseline, int tracking,
                    int reveal_x, int ramp, const int dim[3], const int bright[3]) {
	int pen = x;
	for (; *s; s++) {
		if (FT_Load_Char(face, (unsigned char)*s, FT_LOAD_RENDER))
			continue;
		FT_Bitmap *bm = &face->glyph->bitmap;
		int gx = pen + face->glyph->bitmap_left;
		int gy = baseline - face->glyph->bitmap_top;
		for (unsigned row = 0; row < bm->rows; row++) {
			for (unsigned col = 0; col < bm->width; col++) {
				unsigned a = bm->buffer[row * bm->pitch + col];
				if (!a)
					continue;
				int ax = gx + (int)col, ay = gy + (int)row;
				double t = ramp > 0 ? ((double)(reveal_x - ax)) / ramp + 0.5 : (ax <= reveal_x);
				if (t < 0) t = 0;
				if (t > 1) t = 1;
				int tr = dim[0] + (int)(t * (bright[0] - dim[0]));
				int tg = dim[1] + (int)(t * (bright[1] - dim[1]));
				int tb = dim[2] + (int)(t * (bright[2] - dim[2]));
				uint8_t br, bgc, bb;
				bg_rgb(ay, v->yres, &br, &bgc, &bb);
				int orr = (tr * a + br * (255 - a)) / 255;
				int org = (tg * a + bgc * (255 - a)) / 255;
				int orb = (tb * a + bb * (255 - a)) / 255;
				put_pixel(fb, v, f, ax, ay, make_pixel(v, orr, org, orb));
			}
		}
		pen += face->glyph->advance.x >> 6;
		if (s[1])
			pen += tracking;
	}
}

static void ft_draw_overlay(uint8_t *fb, struct fb_var_screeninfo *v,
                            struct fb_fix_screeninfo *f, FT_Face face,
                            const char *s, int x, int baseline, int tracking,
                            const int colour[3]) {
	int pen = x;
	for (; *s; s++) {
		if (FT_Load_Char(face, (unsigned char)*s, FT_LOAD_RENDER))
			continue;
		FT_Bitmap *bm = &face->glyph->bitmap;
		int gx = pen + face->glyph->bitmap_left;
		int gy = baseline - face->glyph->bitmap_top;
		for (unsigned row = 0; row < bm->rows; row++) {
			for (unsigned col = 0; col < bm->width; col++) {
				unsigned alpha = bm->buffer[row * bm->pitch + col];
				if (alpha)
					blend_pixel(fb, v, f, gx + (int)col, gy + (int)row,
					            colour[0], colour[1], colour[2], alpha);
			}
		}
		pen += face->glyph->advance.x >> 6;
		if (s[1])
			pen += tracking;
	}
}

// --- bitmap fallback -------------------------------------------------------

static void bmp_text(uint8_t *fb, struct fb_var_screeninfo *v, struct fb_fix_screeninfo *f,
                     int x, int y, const char *text, int scale, uint32_t pixel) {
	for (const char *p = text; *p; p++, x += 6 * scale) {
		const Glyph *g = glyph_for(*p);
		for (int row = 0; row < 7; row++)
			for (int col = 0; col < 5; col++)
				if (g->rows[row] & (1 << (4 - col)))
					for (int sy = 0; sy < scale; sy++)
						for (int sx = 0; sx < scale; sx++)
							put_pixel(fb, v, f, x + col * scale + sx, y + row * scale + sy, pixel);
	}
}

// --- what lies under the pill ------------------------------------------------
//
// Every message is its own fbsplash process, and each pill composites over
// whatever is on screen. A narrower pill drawn after a wider one would leave
// the wider one's ends showing ("INSTAL… ADD FRONTEND TO SD CARD …ER ON"). So
// each pill leaves behind, in RAM only, the pixels it covered and the pixels
// it drew; the next one puts the old background back before drawing its own.
//
// The record lives on tmpfs (main() only enables it when /run is one), never on
// the card or /data: it describes this power-on's screen and nothing else. It
// is written in the same logical, upright coordinates the pill is drawn in, so
// rotated panels need nothing special. The background is only put back if the
// screen still shows exactly the pill that was drawn there; if anything else has
// drawn over it since (the frontend, the charger), the record is stale and is
// ignored. NULL disables the whole mechanism.
static const char *under_path;

#define UNDER_MAGIC "BOSPILL1"

typedef struct {
	char magic[8];
	uint32_t xres, yres, bpp;
	int32_t x, y, w, h;
} UnderHeader;

// Copy a rectangle between a framebuffer-stride buffer and a packed one.
static void pack_rect(uint8_t *packed, uint8_t *buf, const struct fb_var_screeninfo *v,
                      const struct fb_fix_screeninfo *f, int x, int y, int w, int h, int to_packed) {
	size_t bytes = v->bits_per_pixel / 8, row = (size_t)w * bytes;
	for (int i = 0; i < h; i++) {
		size_t off = (size_t)(y + i) * f->line_length + (size_t)x * bytes;
		if (to_packed)
			memcpy(packed + i * row, buf + off, row);
		else
			memcpy(buf + off, packed + i * row, row);
	}
}

static int rect_equals(const uint8_t *buf, const struct fb_var_screeninfo *v,
                       const struct fb_fix_screeninfo *f, int x, int y, int w, int h,
                       const uint8_t *packed) {
	size_t bytes = v->bits_per_pixel / 8, row = (size_t)w * bytes;
	for (int i = 0; i < h; i++)
		if (memcmp(buf + (size_t)(y + i) * f->line_length + (size_t)x * bytes,
		           packed + i * row, row))
			return 0;
	return 1;
}

// The previous pill's record, or NULL when there is none or it does not
// describe this screen. On success *hdr holds its rectangle and the returned
// buffer holds the background followed by the drawn pill, both packed.
static uint8_t *under_load(const struct fb_var_screeninfo *v, UnderHeader *hdr) {
	if (!under_path)
		return NULL;
	FILE *fp = fopen(under_path, "rb");
	if (!fp)
		return NULL;
	uint8_t *pixels = NULL;
	if (fread(hdr, sizeof(*hdr), 1, fp) == 1 &&
	    !memcmp(hdr->magic, UNDER_MAGIC, sizeof(hdr->magic)) &&
	    hdr->xres == v->xres && hdr->yres == v->yres && hdr->bpp == v->bits_per_pixel &&
	    hdr->x >= 0 && hdr->y >= 0 && hdr->w > 0 && hdr->h > 0 &&
	    hdr->x + hdr->w <= (int)v->xres && hdr->y + hdr->h <= (int)v->yres) {
		size_t len = (size_t)hdr->w * hdr->h * (v->bits_per_pixel / 8) * 2;
		pixels = malloc(len);
		if (pixels && fread(pixels, 1, len, fp) != len) {
			free(pixels);
			pixels = NULL;
		}
	}
	fclose(fp);
	return pixels;
}

// Replace the record atomically; on any failure leave none, since a record
// that no longer matches the screen is worse than no record.
static void under_save(const struct fb_var_screeninfo *v, int x, int y, int w, int h,
                       const uint8_t *under, const uint8_t *drawn) {
	if (!under_path)
		return;
	unlink(under_path);
	if (!under || !drawn)
		return;
	char tmp[256];
	if (snprintf(tmp, sizeof(tmp), "%s.tmp", under_path) >= (int)sizeof(tmp))
		return;
	FILE *fp = fopen(tmp, "wb");
	if (!fp)
		return;
	UnderHeader hdr;
	memset(&hdr, 0, sizeof(hdr));
	memcpy(hdr.magic, UNDER_MAGIC, sizeof(hdr.magic));
	hdr.xres = v->xres;
	hdr.yres = v->yres;
	hdr.bpp = v->bits_per_pixel;
	hdr.x = x; hdr.y = y; hdr.w = w; hdr.h = h;
	size_t len = (size_t)w * h * (v->bits_per_pixel / 8);
	int ok = fwrite(&hdr, sizeof(hdr), 1, fp) == 1 &&
	         fwrite(under, 1, len, fp) == len && fwrite(drawn, 1, len, fp) == len;
	ok = fclose(fp) == 0 && ok;
	if (!ok || rename(tmp, under_path) != 0)
		unlink(tmp);
}

static void render_pill(uint8_t *fb, struct fb_var_screeninfo *vp,
                        struct fb_fix_screeninfo *fp, int prog, const char *msg) {
	struct fb_var_screeninfo v = *vp;
	struct fb_fix_screeninfo f = *fp;
	int W = (int)v.xres, H = (int)v.yres;
	if (!msg || !msg[0])
		msg = "WORKING";

	const int border[3] = {0x5D, 0x68, 0x6D};
	const int panel[3] = {0x13, 0x18, 0x1B};
	const int track[3] = {0x32, 0x3B, 0x40};
	const int text[3] = {0xEA, 0xF0, 0xF0};
	const int accent[3] = {0xB9, 0xC8, 0xC8};
	int show_progress = prog >= 0;

	FT_Library lib;
	FT_Face face;
	int have_ft = 0;
	int font_size = clampi((int)(H * 0.038), 15, 26);
	int tracking = clampi(font_size / 14, 1, 2);
	int text_width = 0;
	if (!FT_Init_FreeType(&lib) && !FT_New_Face(lib, FONT_PATH, 0, &face)) {
		have_ft = 1;
		while (font_size > 12) {
			FT_Set_Pixel_Sizes(face, 0, font_size);
			text_width = ft_measure(face, msg, tracking);
			if (text_width <= W - 72)
				break;
			font_size--;
		}
	} else {
		font_size = W < 600 ? 2 : 3;
		tracking = 0;
		text_width = (int)strlen(msg) * 6 * font_size - font_size;
	}

	int pad_x = clampi((int)(font_size * 1.05), 18, 30);
	int pill_width = clampi(text_width + pad_x * 2, W * 2 / 5, W - 32);
	int pill_height = clampi(have_ft ? font_size * 3 : font_size * 18, 48, 74);
	int pill_x = (W - pill_width) / 2;
	int pill_y = H - pill_height - clampi(H / 12, 34, 64);
	int radius = pill_height / 2;

	// Compose off-screen and present in one blit. Drawing straight into the
	// mapped framebuffer means the panel is scanning out every intermediate
	// step: the background fill erases the label, then freetype re-renders it
	// glyph by glyph, and repeated progress repaints read as a flicker across
	// the text. One buffer and one memcpy make each repaint atomic to the eye.
	// (NextUI's show2 does the same thing through SDL — redraw everything into
	// an off-screen surface, present once — but with a long-lived process,
	// which §5 of docs/04 rules out here.)
	int box_x = clampi(pill_x - 1, 0, W - 1), box_y = clampi(pill_y - 1, 0, H - 1);
	int box_w = clampi(pill_width + 2, 1, W - box_x);
	int box_h = clampi(pill_height + 2, 1, H - box_y);
	size_t map_len = f.smem_len ? f.smem_len : (size_t)f.line_length * v.yres;
	UnderHeader old;
	uint8_t *old_pixels = under_load(&v, &old);
	uint8_t *canvas = malloc(map_len);
	if (canvas) {
		// Seeding the canvas from the framebuffer is load-bearing, not a
		// wasted read. What goes back is a rectangle, but the pill is a
		// rounded shape: the corners, and the antialiased edge blended
		// against them, must already hold the boot-logo pixels or the blit
		// would stamp a black box around the pill. It is also what keeps
		// round_rect's alpha blending compositing over the real background.
		blit_rect(canvas, fb, &v, &f, box_x, box_y, box_w, box_h);
		if (old_pixels)
			blit_rect(canvas, fb, &v, &f, old.x, old.y, old.w, old.h);
	} else {
		canvas = fb; // Never fail to draw; a visible flicker beats no status.
	}

	// Put back what the previous pill covered, if the screen still shows that
	// pill. Done on the canvas, so the old pill gives way to the new one in the
	// same blit with no bare-logo frame in between.
	size_t old_len = old_pixels ? (size_t)old.w * old.h * (v.bits_per_pixel / 8) : 0;
	if (old_pixels && rect_equals(canvas, &v, &f, old.x, old.y, old.w, old.h,
	                              old_pixels + old_len))
		pack_rect(old_pixels, canvas, &v, &f, old.x, old.y, old.w, old.h, 0);
	else if (old_pixels) {
		free(old_pixels);
		old_pixels = NULL; // Stale: something else has drawn there since.
	}

	// The box is now background only; keep it for the next message.
	size_t box_len = (size_t)box_w * box_h * (v.bits_per_pixel / 8);
	uint8_t *under = under_path ? malloc(box_len * 2) : NULL;
	if (under)
		pack_rect(under, canvas, &v, &f, box_x, box_y, box_w, box_h, 1);

	// Opaque enough for legibility over arbitrary custom artwork, with a quiet
	// one-pixel edge that keeps the pill distinct on dark boot logos.
	round_rect(canvas, &v, &f, pill_x, pill_y, pill_width, pill_height,
	           radius, border, 245);
	round_rect(canvas, &v, &f, pill_x + 1, pill_y + 1,
	           pill_width - 2, pill_height - 2, radius - 1, panel, 246);

	int progress_height = clampi(pill_height / 18, 3, 4);
	int progress_x = pill_x + pad_x;
	int progress_width = pill_width - pad_x * 2;
	int progress_y = pill_y + pill_height - clampi(pill_height / 5, 9, 13);
	if (show_progress) {
		int progress_radius = progress_height / 2;
		round_rect(canvas, &v, &f, progress_x, progress_y,
		           progress_width, progress_height, progress_radius, track, 255);
		int fill_width = progress_width * clampi(prog, 0, 100) / 100;
		if (fill_width > 0)
			round_rect(canvas, &v, &f, progress_x, progress_y,
			           fill_width, progress_height, progress_radius, accent, 255);
	}

	if (have_ft) {
		FT_Set_Pixel_Sizes(face, 0, font_size);
		int tx = pill_x + (pill_width - text_width) / 2;
		int content_bottom = show_progress ? progress_y - 2 : pill_y + pill_height;
		int baseline = pill_y + (content_bottom - pill_y) / 2 + font_size * 3 / 8;
		ft_draw_overlay(canvas, &v, &f, face, msg, tx, baseline, tracking, text);
		FT_Done_Face(face);
		FT_Done_FreeType(lib);
	} else {
		int tx = pill_x + (pill_width - text_width) / 2;
		int content_bottom = show_progress ? progress_y - 2 : pill_y + pill_height;
		int ty = pill_y + (content_bottom - pill_y - 7 * font_size) / 2;
		bmp_text(canvas, &v, &f, tx, ty, msg, font_size,
		         make_pixel(&v, text[0], text[1], text[2]));
	}

	if (under)
		pack_rect(under + box_len, canvas, &v, &f, box_x, box_y, box_w, box_h, 1);
	under_save(&v, box_x, box_y, box_w, box_h, under, under ? under + box_len : NULL);
	free(under);

	if (canvas != fb) {
		if (old_pixels)
			blit_rect(fb, canvas, &v, &f, old.x, old.y, old.w, old.h);
		blit_rect(fb, canvas, &v, &f, box_x, box_y, box_w, box_h);
		free(canvas);
	}
	free(old_pixels);
}

static void render(uint8_t *fb, struct fb_var_screeninfo *vp, struct fb_fix_screeninfo *fp, int prog, const char *msg) {
	struct fb_var_screeninfo v = *vp;
	struct fb_fix_screeninfo f = *fp;
	int W = (int)v.xres, H = (int)v.yres;

	// Ground.
	for (int y = 0; y < H; y++) {
		uint8_t r, g, b;
		bg_rgb(y, H, &r, &g, &b);
		uint32_t px = make_pixel(&v, r, g, b);
		for (int x = 0; x < W; x++)
			put_pixel(fb, &v, &f, x, y, px);
	}

	const int dim[3] = {0x33, 0x3B, 0x40};
	const int bright[3] = {0xEA, 0xF0, 0xF0};
	const int msgcol[3] = {0x7C, 0x8A, 0x8A};

	FT_Library lib;
	FT_Face face;
	int have_ft = 0;
	if (!FT_Init_FreeType(&lib) && !FT_New_Face(lib, FONT_PATH, 0, &face))
		have_ft = 1;

	if (have_ft) {
		// Wordmark — illuminated to progress.
		int size = clampi((int)(H * 0.085), 18, 200);
		FT_Set_Pixel_Sizes(face, 0, size);
		int tracking = (int)(size * 0.34);
		int wordw = ft_measure(face, "BASE OS", tracking);
		int wx = (W - wordw) / 2;
		int baseline = (int)(H * 0.44 + size * 0.35);
		int ramp = clampi((int)(size * 0.55), 1, 400);
		// Keep the complete B bright at the legacy 0% progress state. The static
		// boot logo uses 100%, illuminating the entire wordmark.
		// Adding half the ramp makes every pixel in B reach the solid-bright end
		// of the soft reveal rather than leaving its right side half illuminated.
		int firstw = ft_measure(face, "B", 0);
		int reveal_start = wx + firstw + ramp / 2;
		int reveal_end = wx + wordw + ramp / 2;
		int reveal_x = reveal_start + (int)((double)prog / 100.0 * (reveal_end - reveal_start));
		ft_draw(fb, &v, &f, face, "BASE OS", wx, baseline, tracking, reveal_x, ramp, dim, bright);

		// Optional status/error message (solid dim, centred below).
		if (msg && strcmp(msg, "\xE2\x9C\x93") == 0) {
			draw_check(fb, &v, &f, msgcol);
		} else if (msg) {
			int ms = clampi((int)(H * 0.045), 12, 80);
			FT_Set_Pixel_Sizes(face, 0, ms);
			int mt = (int)(ms * 0.12);
			int mw = ft_measure(face, msg, mt);
			int mx = (W - mw) / 2;
			int mb = (int)(H * 0.63 + ms * 0.35);
			ft_draw(fb, &v, &f, face, msg, mx, mb, mt, W + 999, 1, msgcol, msgcol);
		}
	} else {
		// Bitmap fallback: wordmark bright + optional message, no illumination.
		int scale = W < 600 ? 4 : 5;
		int tw = (int)strlen("BASE OS") * 6 * scale - scale;
		bmp_text(fb, &v, &f, (W - tw) / 2, (int)(H * 0.42), "BASE OS", scale, make_pixel(&v, 0xEA, 0xF0, 0xF0));
		if (msg && strcmp(msg, "\xE2\x9C\x93") == 0) {
			draw_check(fb, &v, &f, msgcol);
		} else if (msg) {
			int ms = W < 600 ? 2 : 3;
			int mw = (int)strlen(msg) * 6 * ms - ms;
			bmp_text(fb, &v, &f, (W - mw) / 2, (int)(H * 0.60), msg, ms, make_pixel(&v, 0x7C, 0x8A, 0x8A));
		}
	}

	if (have_ft) {
		FT_Done_Face(face);
		FT_Done_FreeType(lib);
	}
}

// --- panel rotation --------------------------------------------------------
//
// The RG28XX carries its 480x640 panel turned a quarter turn and is held in
// landscape, so an image drawn upright in framebuffer coordinates arrives on the
// panel lying on its side. Anbernic's own bootlogo shows the convention: the
// artwork stored in the portrait BMP is the landscape picture turned 90°
// counter-clockwise, and the panel turns it back.
//
// Rather than teach every primitive about orientation, the renderer draws into
// an off-screen buffer in *logical* coordinates — always the upright, as-held
// geometry, so the RG28XX composes at 640x480 exactly like the RG35XX family —
// and turns that buffer onto the framebuffer as the last step. `rot` is the
// counter-clockwise angle of that turn, matching the vendor convention above.
// Every other target passes 0 and takes the original path: no buffer, no copy.

static int rotation_swaps_axes(int rot) { return rot == 90 || rot == 270; }

// Only quarter turns are meaningful; anything else means an unrotated panel so
// that a malformed profile can never leave a device with no splash at all.
static int normalise_rotation(int rot) {
	rot %= 360;
	if (rot < 0)
		rot += 360;
	return rot == 90 || rot == 180 || rot == 270 ? rot : 0;
}

// Logical geometry: the physical one with its axes swapped on a quarter turn,
// and a tightly packed stride of its own — the framebuffer's may carry padding
// or several buffers' worth of scanout, neither of which applies off-screen.
static void logical_geometry(const struct fb_var_screeninfo *pv,
                             const struct fb_fix_screeninfo *pf, int rot,
                             struct fb_var_screeninfo *lv,
                             struct fb_fix_screeninfo *lf) {
	*lv = *pv;
	*lf = *pf;
	if (rotation_swaps_axes(rot)) {
		lv->xres = pv->yres;
		lv->yres = pv->xres;
	}
	lv->xres_virtual = lv->xres;
	lv->yres_virtual = lv->yres;
	lf->line_length = lv->xres * (pv->bits_per_pixel / 8);
	lf->smem_len = lf->line_length * lv->yres;
}

// Where a logical pixel lands in the framebuffer under a counter-clockwise rot.
static void rotate_point(int rot, int lw, int lh, int lx, int ly, int *px, int *py) {
	switch (rot) {
		case 90:  *px = ly;              *py = lw - 1 - lx; break;
		case 180: *px = lw - 1 - lx;     *py = lh - 1 - ly; break;
		case 270: *px = lh - 1 - ly;     *py = lx;          break;
		default:  *px = lx;              *py = ly;          break;
	}
}

// Copy the logical buffer onto the framebuffer (to_fb) or seed it from the
// framebuffer (!to_fb). Both walk logical pixels through the same mapping, so a
// copy out after a copy in restores every byte that was not drawn over.
static void rotate_blit(uint8_t *fb, const struct fb_var_screeninfo *pv,
                        const struct fb_fix_screeninfo *pf, uint8_t *lbuf,
                        const struct fb_var_screeninfo *lv,
                        const struct fb_fix_screeninfo *lf, int rot, int to_fb) {
	size_t bytes = pv->bits_per_pixel / 8;
	int lw = (int)lv->xres, lh = (int)lv->yres;
	for (int ly = 0; ly < lh; ly++) {
		uint8_t *lrow = lbuf + (size_t)ly * lf->line_length;
		for (int lx = 0; lx < lw; lx++) {
			int px, py;
			rotate_point(rot, lw, lh, lx, ly, &px, &py);
			if (px < 0 || py < 0 || px >= (int)pv->xres || py >= (int)pv->yres)
				continue;
			uint8_t *p = fb + (size_t)py * pf->line_length + (size_t)px * bytes;
			uint8_t *l = lrow + (size_t)lx * bytes;
			if (to_fb)
				memcpy(p, l, bytes);
			else
				memcpy(l, p, bytes);
		}
	}
}

// Draw through the rotation layer. With rot 0 the framebuffer is touched exactly
// as it was before rotation existed.
static void present(uint8_t *fb, struct fb_var_screeninfo *v,
                    struct fb_fix_screeninfo *f, int rot, int prog,
                    const char *msg, int pill) {
	struct fb_var_screeninfo lv;
	struct fb_fix_screeninfo lf;
	uint8_t *canvas = fb;

	if (rot) {
		logical_geometry(v, f, rot, &lv, &lf);
		canvas = malloc(lf.smem_len);
		if (canvas) {
			// The pill composites against what is already on screen — its
			// rounded corners and antialiased edge have to blend with the boot
			// logo underneath — so the logical buffer starts as the framebuffer
			// turned back upright. The full-screen logo overwrites every pixel
			// and needs no seed.
			if (pill)
				rotate_blit(fb, v, f, canvas, &lv, &lf, rot, 0);
		} else {
			// Never fail to draw: a sideways pill still names the work in
			// progress, and no pill at all names nothing.
			canvas = fb;
			rot = 0;
		}
	}

	if (pill) {
		render_pill(canvas, rot ? &lv : v, rot ? &lf : f, prog, msg);
	} else {
		// The full-screen logo covers every pixel, any old pill included.
		if (under_path)
			unlink(under_path);
		render(canvas, rot ? &lv : v, rot ? &lf : f, prog, msg);
	}

	if (canvas != fb) {
		// Turning the whole frame back writes every visible pixel, but the copy
		// in above means the ones outside the pill are written their own values:
		// "preserves every pixel outside the status surface" still holds by
		// value, which is what the guarantee is about.
		rotate_blit(fb, v, f, canvas, &lv, &lf, rot, 1);
		free(canvas);
	}
}

// This renderer takes no options. Reject anything option-shaped rather than
// letting atoi() quietly turn it into progress 0: that is precisely how a
// binary predating the pill renderer, handed the `--pill 45 MESSAGE` of the
// day's wrapper, painted the full-screen logo with "45" as its caption and
// dropped the real message. A caller built against a different contract must
// fail loudly and draw nothing, leaving the bootloader logo intact.
// `-1` is a progress value, not an option, and passes.
static int reject_options(int argc, char **argv) {
	for (int i = 1; i < argc; i++)
		if (argv[i][0] == '-' && argv[i][1] == '-')
			return 1;
	return 0;
}

#ifdef FBSPLASH_TEST
// Offline render harness: draw to an in-memory 640x480 BGRA buffer and write a
// PPM so the splash can be eyeballed without a real framebuffer or device.
//
// W and H are the panel's own dimensions, as the device's framebuffer reports
// them, and the rotation argument is the panel's — so the PPM is byte-for-byte
// what a device would scan out. That is what make-bootlogo.sh needs; it also
// means a rotated target's preview looks sideways in an image viewer and
// upright on the hardware. Render with rotation 0 to eyeball the design itself.
//
// Further <progress> <message> pairs after the rotation draw more pills on top,
// in order, the way successive fbsplash runs do on a device; the record of what
// lies under each pill goes to <out>.under, standing in for the one in /run.
// FBSPLASH_TEST_NO_UNDER=1 draws without that record, as builds before it did.
#include <arpa/inet.h>
int main(int argc, char **argv) {
	if (reject_options(argc, argv)) {
		fprintf(stderr, "fbsplash: no options are accepted\n");
		return 2;
	}
	int prog = argc > 1 ? atoi(argv[1]) : 0;
	const char *msg = (argc > 2 && argv[2][0]) ? argv[2] : NULL;
	int pill = msg != NULL;
	prog = pill && prog < 0 ? -1 : clampi(prog, 0, 100);
	const char *out = argc > 3 ? argv[3] : "/out/render.ppm";
	int W = argc > 4 ? atoi(argv[4]) : 640;
	int H = argc > 5 ? atoi(argv[5]) : 480;
	int rot = normalise_rotation(argc > 6 ? atoi(argv[6]) : 0);
	W = clampi(W, 64, 4096);
	H = clampi(H, 64, 4096);

	struct fb_var_screeninfo v;
	struct fb_fix_screeninfo f;
	memset(&v, 0, sizeof(v));
	memset(&f, 0, sizeof(f));
	v.xres = W; v.yres = H; v.bits_per_pixel = 32;
	v.red.offset = 16; v.red.length = 8;
	v.green.offset = 8; v.green.length = 8;
	v.blue.offset = 0; v.blue.length = 8;
	f.line_length = W * 4;
	f.smem_len = f.line_length * H;

	char under[4096];
	snprintf(under, sizeof(under), "%s.under", out);
	if (!getenv("FBSPLASH_TEST_NO_UNDER"))
		under_path = under;

	uint8_t *fb = calloc(1, f.smem_len);
	if (pill)
		// Give offline previews the default static logo beneath the overlay.
		present(fb, &v, &f, rot, 100, NULL, 0);
	present(fb, &v, &f, rot, prog, msg, pill);
	for (int i = 7; i + 1 < argc; i += 2) {
		int next = atoi(argv[i]);
		const char *next_msg = argv[i + 1];
		present(fb, &v, &f, rot, next < 0 ? -1 : clampi(next, 0, 100),
		        next_msg[0] ? next_msg : NULL, next_msg[0] != 0);
	}
	unlink(under);

	FILE *fp = fopen(out, "wb");
	fprintf(fp, "P6\n%d %d\n255\n", W, H);
	for (int y = 0; y < H; y++) {
		for (int x = 0; x < W; x++) {
			uint32_t px = *(uint32_t *)(fb + y * f.line_length + x * 4);
			uint8_t rgb[3] = {(px >> 16) & 0xff, (px >> 8) & 0xff, px & 0xff};
			fwrite(rgb, 1, 3, fp);
		}
	}
	fclose(fp);
	free(fb);
	return 0;
}
#else
#include <linux/magic.h>
#include <sys/vfs.h>

// The panel rotation is device identity, so it is read from the file the build
// already generates for the target and model rather than from a second source of
// truth or a new argument (the argument list is a contract — see
// reject_options()). A file that is missing, unreadable or silent about rotation
// means 0: every unrotated target, and any rootfs built before the key existed.
static int panel_rotation(void) {
	static const char key[] = "BASEOS_PANEL_ROTATION_CCW=";
	FILE *fp = fopen("/etc/baseos-release", "r");
	if (!fp)
		return 0;
	char line[160];
	int rot = 0;
	while (fgets(line, sizeof(line), fp)) {
		if (strncmp(line, key, sizeof(key) - 1) == 0) {
			rot = atoi(line + sizeof(key) - 1);
			break;
		}
	}
	fclose(fp);
	return normalise_rotation(rot);
}

int main(int argc, char **argv) {
	if (reject_options(argc, argv)) {
		fprintf(stderr, "fbsplash: no options are accepted\n");
		return 2;
	}
	int prog = argc > 1 ? atoi(argv[1]) : 0;
	const char *msg = (argc > 2 && argv[2][0]) ? argv[2] : NULL;
	int pill = msg != NULL;
	prog = pill && prog < 0 ? -1 : clampi(prog, 0, 100);

	// The record of what lies under the pill is screen state for this power-on
	// only. Keep it solely where it cannot reach the card: if /run is not
	// tmpfs, go without (a wider old pill may then show around a narrower one).
	struct statfs run;
	if (statfs("/run", &run) == 0 && run.f_type == TMPFS_MAGIC)
		under_path = "/run/baseos-splash-under";

	int fd = open("/dev/fb0", O_RDWR);
	if (fd < 0)
		return 1;
	struct fb_var_screeninfo v;
	struct fb_fix_screeninfo f;
	if (ioctl(fd, FBIOGET_VSCREENINFO, &v) < 0 || ioctl(fd, FBIOGET_FSCREENINFO, &f) < 0) {
		close(fd);
		return 1;
	}
	if (v.bits_per_pixel != 16 && v.bits_per_pixel != 24 && v.bits_per_pixel != 32) {
		close(fd);
		return 1;
	}
	size_t map_len = f.smem_len ? f.smem_len : (size_t)f.line_length * v.yres;
	uint8_t *fb = mmap(NULL, map_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (fb == MAP_FAILED) {
		close(fd);
		return 1;
	}

	present(fb, &v, &f, panel_rotation(), prog, msg, pill);
	msync(fb, map_len, MS_SYNC);

	// Force the panel to scan out this buffer: on Allwinner disp2 "smooth boot"
	// kernels the display can stay latched to U-Boot's logo until a pan/unblank.
	ioctl(fd, FBIOBLANK, FB_BLANK_UNBLANK);
	v.xoffset = 0;
	v.yoffset = 0;
	if (ioctl(fd, FBIOPAN_DISPLAY, &v) < 0) {
		v.activate = FB_ACTIVATE_NOW | FB_ACTIVATE_FORCE;
		ioctl(fd, FBIOPUT_VSCREENINFO, &v);
	}

	munmap(fb, map_len);
	close(fd);
	return 0;
}
#endif
