#ifndef BASEOS_SEQUENTIAL_SLOT_READER_H
#define BASEOS_SEQUENTIAL_SLOT_READER_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "boot-blob.h"

/* Return 1 only after every requested 512-byte sector arrived. */
struct baseos_seq_io {
    void *context;
    int (*read)(void *, uint32_t lba, uint32_t sectors, void *out);
    uint64_t sectors;
};

struct baseos_seq_policy {
    uint32_t model_id;
    unsigned char native_abi[32], kernel_sha256[32];
    /* NULL for installed firmware: /init compares the returned mutable ID
     * against selected p5's /etc/baseos/boot-payload-id before switch_root. */
    const unsigned char *expected_root_build_id;
};

struct baseos_seq_slot {
    uint32_t root_lba, root_sectors, descriptor_lba, image_lba, image_bytes;
    unsigned slot; /* A=0, B=1; derived solely from primary GPT entry 5. */
    struct baseos_seq_header header; /* Exact little-endian descriptor bytes. */
};

static uint32_t baseos_seq_u32(const unsigned char *p)
{
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint64_t baseos_seq_u64(const unsigned char *p)
{
    return baseos_seq_u32(p) | (uint64_t)baseos_seq_u32(p + 4) << 32;
}

static int baseos_seq_zero(const unsigned char *p, size_t size)
{
    unsigned char any = 0;
    while (size--) any |= *p++;
    return any == 0;
}

static uint32_t baseos_seq_fnv(const unsigned char *p, size_t size, size_t zero_at)
{
    uint32_t hash = 2166136261u;
    for (size_t i = 0; i < size; ++i)
        hash = (hash ^ (i >= zero_at && i - zero_at < 4 ? 0 : p[i])) * 16777619u;
    return hash;
}

static uint32_t baseos_seq_crc(const unsigned char *p, size_t size, size_t zero_at)
{
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < size; ++i) {
        crc ^= i >= zero_at && i - zero_at < 4 ? 0 : p[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static int baseos_seq_read(const struct baseos_seq_io *io, uint64_t lba,
                            uint32_t sectors, void *out)
{
    return sectors && lba < io->sectors && sectors <= io->sectors - lba &&
        lba + sectors <= 0x100000000ull &&
        io->read(io->context, (uint32_t)lba, sectors, out) == 1;
}

static int baseos_seq_name(const unsigned char *entry, const char *name)
{
    unsigned i = 0;
    for (; name[i]; ++i)
        if (i >= 35 || entry[56 + i * 2] != (unsigned char)name[i] || entry[57 + i * 2]) return 0;
    return entry[56 + i * 2] == 0 && entry[57 + i * 2] == 0;
}

/* At most four callback reads: primary header, two-sector entry table,
 * selected p5's fixed tail descriptor, then its single contiguous payload.
 * No backup-GPT selection, inactive-slot retry, or media writes occur.
 * On failure no slot is published; destination may contain an invalid prefix. */
static int baseos_seq_load(const struct baseos_seq_io *io,
                            const struct baseos_seq_policy *policy,
                            void *destination, uint32_t capacity,
                            struct baseos_seq_slot *out)
{
    if (!out) return 0;
    memset(out, 0, sizeof(*out));
    if (!io || !io->read || io->sectors < 4096 || io->sectors > 0x100000000ull ||
        !policy || policy->model_id < 1 || policy->model_id > 11 ||
        baseos_seq_zero(policy->native_abi, 32) || baseos_seq_zero(policy->kernel_sha256, 32) ||
        !destination || (uintptr_t)destination % 4 || capacity > BASEOS_SEQ_IMAGE_CAPACITY) return 0;
    uint32_t header_words[128], table_words[256], descriptor_words[128];
    unsigned char *h = (unsigned char *)header_words, *table = (unsigned char *)table_words;
    unsigned char *d = (unsigned char *)descriptor_words;
    if (!baseos_seq_read(io, 1, 1, h) || memcmp(h, "EFI PART", 8) ||
        baseos_seq_u32(h + 8) != 0x10000 || baseos_seq_u32(h + 12) != 92 ||
        baseos_seq_u32(h + 20) || baseos_seq_u64(h + 24) != 1 ||
        baseos_seq_u64(h + 32) != io->sectors - 1 ||
        baseos_seq_crc(h, 92, 16) != baseos_seq_u32(h + 16) ||
        baseos_seq_u32(h + 80) != 8 || baseos_seq_u32(h + 84) != 128) return 0;
    uint64_t first = baseos_seq_u64(h + 40), last = baseos_seq_u64(h + 48);
    uint64_t entries = baseos_seq_u64(h + 72);
    if (first < 4 || last < first || last >= io->sectors - 3 ||
        entries < 2 || entries > first - 2 ||
        !baseos_seq_read(io, entries, 2, table) ||
        baseos_seq_crc(table, 1024, (size_t)-1) != baseos_seq_u32(h + 88)) return 0;
    uint64_t starts[8], ends[8];
    for (unsigned i = 0; i < 8; ++i) {
        const unsigned char *e = table + i * 128;
        starts[i] = ends[i] = 0;
        if (baseos_seq_zero(e, 16)) {
            if (!baseos_seq_zero(e, 128)) return 0;
            continue;
        }
        starts[i] = baseos_seq_u64(e + 32); ends[i] = baseos_seq_u64(e + 40);
        if (starts[i] < first || ends[i] < starts[i] || ends[i] > last) return 0;
        for (unsigned j = 0; j < i; ++j)
            if (starts[j] && starts[i] <= ends[j] && starts[j] <= ends[i]) return 0;
    }
    const unsigned char *boot = table + 3 * 128, *root = table + 4 * 128, *after = table + 5 * 128;
    if (!starts[3] || !starts[4] || !starts[5] || !baseos_seq_name(boot, "boot") ||
        !baseos_seq_name(root, "rootfs") || !baseos_seq_name(after, "UDISK")) return 0;
    uint64_t sectors = BASEOS_SEQ_SLOT_BYTES / 512, base = ends[3] + 1;
    if (ends[4] - starts[4] + 1 != sectors || base > last ||
        2 * sectors > last - base + 1 || starts[5] != base + 2 * sectors ||
        (starts[4] != base && starts[4] != base + sectors)) return 0;
    for (unsigned i = 0; i < 8; ++i)
        if (i != 4 && starts[i] && starts[i] < base + 2 * sectors && ends[i] >= base) return 0;
    uint64_t descriptor = starts[4] + BASEOS_SEQ_TAIL_OFFSET / 512;
    if (!baseos_seq_read(io, descriptor, 1, d) ||
        memcmp(d, BASEOS_SEQ_MAGIC, 8) || baseos_seq_u32(d + 8) != BASEOS_SEQ_VERSION ||
        baseos_seq_u32(d + 12) != BASEOS_SEQ_HEADER_BYTES ||
        baseos_seq_u32(d + 16) != policy->model_id ||
        baseos_seq_fnv(d, 512, 44) != baseos_seq_u32(d + 44) ||
        baseos_seq_u64(d + 48) != BASEOS_SEQ_SLOT_BYTES ||
        baseos_seq_u64(d + 56) != BASEOS_SEQ_TAIL_OFFSET ||
        memcmp(d + 64, policy->native_abi, 32) || baseos_seq_zero(d + 96, 32) ||
        (policy->expected_root_build_id && memcmp(d + 96, policy->expected_root_build_id, 32)) ||
        memcmp(d + 160, policy->kernel_sha256, 32) || !baseos_seq_zero(d + 256, 256)) return 0;
    uint32_t bytes = baseos_seq_u32(d + 20), initrd = baseos_seq_u32(d + 28), library = baseos_seq_u32(d + 36);
    if (!bytes || bytes % 2048 || bytes > BASEOS_SEQ_TAIL_BYTES - 512 ||
        bytes > capacity || !initrd || initrd > BASEOS_SEQ_INITRD_CAPACITY ||
        initrd > bytes || !library || library > initrd ||
        !baseos_seq_read(io, descriptor + 1, bytes / 512, destination) ||
        baseos_seq_fnv(destination, bytes, (size_t)-1) != baseos_seq_u32(d + 24)) return 0;
    out->root_lba = (uint32_t)starts[4]; out->root_sectors = (uint32_t)sectors;
    out->descriptor_lba = (uint32_t)descriptor; out->image_lba = (uint32_t)(descriptor + 1);
    out->image_bytes = bytes; out->slot = starts[4] != base;
    memcpy(&out->header, d, sizeof(out->header));
    return 1;
}

#endif
