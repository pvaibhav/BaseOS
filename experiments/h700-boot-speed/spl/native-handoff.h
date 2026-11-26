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
#endif
