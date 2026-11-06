#ifndef BASEOS_NATIVE_HANDOFF_H
#define BASEOS_NATIVE_HANDOFF_H

#define BASEOS_NATIVE_MAGIC 0x314e5342u
#define BASEOS_NATIVE_RECORD_VERSION 2u
#define BASEOS_NATIVE_RECORD_BYTES 240u
#define BASEOS_NATIVE_RECORD_ADDRESS 0x4a300000u
#define BASEOS_NATIVE_IMAGE_ADDRESS 0x45000000u
#define BASEOS_NATIVE_IMAGE_CAPACITY 0x01000000u
#define BASEOS_NATIVE_FDT_ADDRESS 0x44000000u
#define BASEOS_NATIVE_FDT_CAPACITY 0x00100000u

enum baseos_boot_reason {
    BASEOS_BOOT_BUTTON, BASEOS_BOOT_IRQ, BASEOS_BOOT_USB,
    BASEOS_BOOT_CHARGER, BASEOS_BOOT_BATTERY, BASEOS_BOOT_UNKNOWN
};
enum baseos_boot_reason_source {
    BASEOS_BOOT_SOURCE_UNKNOWN, BASEOS_BOOT_SOURCE_POWERON,
    BASEOS_BOOT_SOURCE_PMU_FALLBACK
};

static inline int baseos_boot_reason_valid(unsigned int reason, unsigned int source)
{
    return (source == BASEOS_BOOT_SOURCE_UNKNOWN && reason == BASEOS_BOOT_UNKNOWN) ||
           (source == BASEOS_BOOT_SOURCE_POWERON && reason < BASEOS_BOOT_UNKNOWN) ||
           (source == BASEOS_BOOT_SOURCE_PMU_FALLBACK && reason == BASEOS_BOOT_CHARGER);
}

/* Fixed little-endian data only. Both firmware compilers check this layout. */
struct baseos_native_record {
    unsigned int magic, version, bytes, model;
    unsigned long long ram_bytes;
    unsigned long long started_ticks, read_started_ticks, loaded_ticks;
    unsigned int dram_type;
    unsigned int dram_words[32];
    unsigned char board[8];
    unsigned int image_bytes, image_fnv, record_fnv;
    unsigned char generation[32];
    unsigned int boot_reason, boot_reason_source;
};
_Static_assert(sizeof(unsigned int) == 4, "native word width");
_Static_assert(sizeof(struct baseos_native_record) == BASEOS_NATIVE_RECORD_BYTES, "native record ABI");
_Static_assert(__builtin_offsetof(struct baseos_native_record, dram_words) == 52,
               "native DRAM ABI");
_Static_assert(__builtin_offsetof(struct baseos_native_record, record_fnv) == 196,
               "native checksum ABI");
_Static_assert(__builtin_offsetof(struct baseos_native_record, boot_reason) == 232,
               "native boot reason ABI");
_Static_assert(__builtin_offsetof(struct baseos_native_record, boot_reason_source) == 236,
               "native boot reason source ABI");
/* BOS3 v1: one Android-v2 image and one independent mainline FDT. LE ARM ABI.
 * Integrity detects damaged media; this is not an authentication format. */
struct baseos_mainline_header {
    unsigned int magic, version, total, body_fnv;
    unsigned int android_offset, android_bytes, dtb_offset, dtb_bytes;
    unsigned char generation[32];
    unsigned int header_fnv;
    unsigned char reserved[444];
};
_Static_assert(sizeof(struct baseos_mainline_header) == 512, "BOS3 sector ABI");
static inline unsigned int baseos_payload_hash(const void *data, unsigned int bytes)
{
    const unsigned char *p = data;
    unsigned int h = 2166136261u;
    while (bytes--) h = (h ^ *p++) * 16777619u;
    return h;
}
static inline int baseos_payload_header(const struct baseos_mainline_header *h,
                                        const unsigned char *generation)
{
    unsigned int hash = 2166136261u;
    const unsigned char *p = (const void *)h;
    for (unsigned int i = 0; i < 512; i++)
        hash = (hash ^ (i >= 64 && i < 68 ? 0 : p[i])) * 16777619u;
    if (h->magic != 0x33534f42u || h->version != 1 || hash != h->header_fnv ||
        h->total < 1024 || h->total > BASEOS_NATIVE_IMAGE_CAPACITY || h->total % 512 ||
        h->android_offset != 512 || h->android_bytes < 2048 || h->android_bytes % 512 ||
        h->android_bytes > h->total - 512 ||
        h->dtb_offset != 512 + h->android_bytes || !h->dtb_bytes ||
        h->dtb_bytes > BASEOS_NATIVE_FDT_CAPACITY - 4096 ||
        h->dtb_offset > h->total || h->dtb_bytes > h->total - h->dtb_offset ||
        h->total - h->dtb_offset - h->dtb_bytes >= 512) return 0;
    for (unsigned int i = 0; i < 32; i++) if (h->generation[i] != generation[i]) return 0;
    for (unsigned int i = 0; i < sizeof(h->reserved); i++) if (h->reserved[i]) return 0;
    return 1;
}
#endif
