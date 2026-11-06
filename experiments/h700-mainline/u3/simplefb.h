#ifndef BASEOS_U3_SIMPLEFB_H
#define BASEOS_U3_SIMPLEFB_H
#include <stddef.h>
#include <stdint.h>
#include "helper.h"
struct baseos_u3_range { uint64_t start, end; };
/* Trusted generated model policy, never values read from the input FDT. */
struct baseos_u3_fb_layout {
 uint32_t width, height, stride, from_ram_end;
 const struct baseos_u3_range *arenas;
 size_t arena_count;
};
/* r and m must already have passed baseos_u2_record/prepare validation. */
int baseos_u3_simplefb(void *fdt, size_t capacity,
 const struct baseos_native_record *r, const struct baseos_u2_metadata *m,
 const struct baseos_u3_fb_layout *layout);
#endif
