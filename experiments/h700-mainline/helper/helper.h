#ifndef BASEOS_U2_HELPER_H
#define BASEOS_U2_HELPER_H
#include <stdint.h>
#include "native-handoff.h"
#include "baseos-bl31-handoff.h"
struct baseos_u2_arenas {
 const unsigned char *image;
 unsigned char *kernel, *initrd;
 void *fdt;
 struct baseos_bl31_handoff *handoff;
 unsigned char *t4;
};
struct baseos_u2_metadata {
 char serial[20];
 uint32_t panel;
 uint64_t times[7], logo_address;
 const void *display;
 uint32_t display_bytes;
};
int baseos_u2_record(const struct baseos_native_record *r);
int baseos_u2_prepare(const struct baseos_u2_arenas *a, const struct baseos_native_record *r,
                     struct baseos_u2_metadata *m);
int baseos_u2_fdt(void *, const void *, uint32_t, const struct baseos_native_record *,
                  uint32_t, const struct baseos_u2_metadata *);
#endif
