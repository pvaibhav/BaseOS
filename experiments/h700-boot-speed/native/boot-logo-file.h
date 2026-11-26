#ifndef BASEOS_BOOT_LOGO_FILE_H
#define BASEOS_BOOT_LOGO_FILE_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* The callback returns 1 only after all requested 512-byte sectors arrive.
 * Buffers are word-aligned. No callback may retain them after returning. */
struct baseos_logo_io {
    void *context;
    int (*read)(void *, uint32_t lba, uint32_t blocks, void *out);
};

struct baseos_logo_fat {
    const struct baseos_logo_io *io;
    uint32_t base, sectors, fat, fat_sectors, data, last, spc, eoc;
    uint32_t cached, calls, cache[128];
};

static uint32_t baseos_logo_le16(const uint8_t *p)
{
    return p[0] | (uint32_t)p[1] << 8;
}

static uint32_t baseos_logo_le32(const uint8_t *p)
{
    return baseos_logo_le16(p) | baseos_logo_le16(p + 2) << 16;
}

static int baseos_logo_read(struct baseos_logo_fat *f, uint32_t sector,
                            uint32_t blocks, void *out)
{
    if (!blocks || sector >= f->sectors || blocks > f->sectors - sector || !f->calls) return 0;
    --f->calls;
    return f->io->read(f->io->context, f->base + sector, blocks, out) == 1;
}

static int baseos_logo_cluster(const struct baseos_logo_fat *f, uint32_t cluster)
{
    return cluster >= 2 && cluster <= f->last && cluster < f->eoc - 8;
}

static int baseos_logo_next(struct baseos_logo_fat *f, uint32_t cluster, uint32_t *next)
{
    uint32_t width = f->eoc == 0xfff8 ? 2 : 4;
    uint32_t sector = cluster / (512 / width);
    if (sector >= f->fat_sectors) return 0;
    if (f->cached != sector) {
        if (!baseos_logo_read(f, f->fat + sector, 1, f->cache)) return 0;
        f->cached = sector;
    }
    const uint8_t *entry = (const uint8_t *)f->cache + cluster % (512 / width) * width;
    *next = width == 2 ? baseos_logo_le16(entry) : baseos_logo_le32(entry) & 0x0fffffff;
    return 1;
}

/* Read exact root 8.3 BOOTLOGO.BMP from FAT16/FAT32 within the supplied partition.
 * capacity is at most 4 MiB; destination must be word-aligned. The final sector
 * is read in full, so its rounded extent must also fit capacity. Publish bytes
 * only after a complete file and terminal EOC; on failure destination may contain
 * an unpublished prefix. Directory <=128 sectors, file <=8192 clusters, total
 * callback calls <=1024. FAT12 and directories beyond this budget are refused. */
static int baseos_logo_file(const struct baseos_logo_io *io, uint32_t part_lba,
                            uint32_t part_sectors, void *destination,
                            uint32_t capacity, uint32_t *bytes)
{
    if (!bytes) return 0;
    *bytes = 0;
    if (!io || !io->read || !destination || (uintptr_t)destination % 4 ||
        capacity < 512 || capacity > 4 * 1024 * 1024 || !part_sectors ||
        (uint64_t)part_lba + part_sectors > 0x100000000ull) return 0;
    struct baseos_logo_fat f;
    f.io = io; f.base = part_lba; f.sectors = part_sectors;
    f.calls = 1024; f.cached = UINT32_MAX;
    uint32_t scratch[128];
    uint8_t *b = (uint8_t *)scratch;
    if (!baseos_logo_read(&f, 0, 1, b) || b[510] != 0x55 || b[511] != 0xaa ||
        baseos_logo_le16(b + 11) != 512) return 0;
    f.spc = b[13];
    uint32_t reserved = baseos_logo_le16(b + 14), fats = b[16];
    uint32_t roots = baseos_logo_le16(b + 17), total16 = baseos_logo_le16(b + 19);
    uint32_t total32 = baseos_logo_le32(b + 32), fat16 = baseos_logo_le16(b + 22);
    f.sectors = total16 ? total16 : total32;
    f.fat_sectors = fat16 ? fat16 : baseos_logo_le32(b + 36);
    if (!f.spc || f.spc > 128 || (f.spc & (f.spc - 1)) || !reserved ||
        (fats != 1 && fats != 2) || (total16 && total32) || !f.fat_sectors ||
        !f.sectors) return 0;
    uint32_t root_sectors = (roots + 15) / 16;
    uint64_t root_sector = (uint64_t)reserved + (uint64_t)fats * f.fat_sectors;
    uint64_t data = root_sector + root_sectors;
    if (data >= f.sectors) return 0;
    f.data = (uint32_t)data;
    uint32_t clusters = (f.sectors - f.data) / f.spc;
    if (clusters < 4085 || clusters > 0x0fffffee) return 0;
    int fat32 = clusters >= 65525;
    if ((uint64_t)(clusters + 2) * (fat32 ? 4 : 2) > (uint64_t)f.fat_sectors * 512) return 0;
    /* Vendor boot-resource BPBs can describe 128 MiB inside a 32 MiB GPT
     * partition. Declared geometry determines FAT type/coverage; GPT and BPB
     * independently bound every readable sector and reachable data cluster. */
    if (f.sectors > part_sectors) f.sectors = part_sectors;
    if (f.data >= f.sectors || (f.sectors - f.data) / f.spc == 0) return 0;
    f.last = (f.sectors - f.data) / f.spc + 1;
    f.fat = reserved; f.eoc = fat32 ? 0x0ffffff8 : 0xfff8;
    uint32_t directory = 0, sector = (uint32_t)root_sector;
    if (fat32) {
        uint32_t flags = baseos_logo_le16(b + 40), active = flags & 0x80 ? flags & 15 : 0;
        if (fat16 || roots || baseos_logo_le16(b + 42) || (flags & ~0x8fu) || active >= fats) return 0;
        f.fat += active * f.fat_sectors;
        directory = baseos_logo_le32(b + 44);
        if (!baseos_logo_cluster(&f, directory)) return 0;
        sector = f.data + (directory - 2) * f.spc;
    } else if (!fat16 || !roots) return 0;
    uint32_t within = 0, file_cluster = 0, file_bytes = 0;
    for (unsigned scanned = 0; scanned < 128; ++scanned) {
        if (!fat32 && !roots) return 0;
        if (!baseos_logo_read(&f, sector, 1, b)) return 0;
        for (unsigned offset = 0; offset < 512; offset += 32) {
            if (!fat32) { if (!roots) return 0; --roots; }
            uint8_t *entry = b + offset;
            if (!entry[0]) return 0;
            if (entry[0] == 0xe5 || entry[11] == 0x0f || memcmp(entry, "BOOTLOGOBMP", 11)) continue;
            if (entry[11] & 0xd8) return 0;
            file_cluster = baseos_logo_le16(entry + 26);
            uint32_t high = baseos_logo_le16(entry + 20);
            if (!fat32 && high) return 0;
            file_cluster |= high << 16;
            file_bytes = baseos_logo_le32(entry + 28);
            goto found;
        }
        ++sector;
        if (fat32 && ++within == f.spc) {
            if (!baseos_logo_next(&f, directory, &directory) || !baseos_logo_cluster(&f, directory)) return 0;
            sector = f.data + (directory - 2) * f.spc; within = 0;
        }
    }
    return 0;
found:
    if (!file_bytes || file_bytes > capacity) return 0;
    uint32_t remaining = (file_bytes + 511) / 512;
    if (remaining > capacity / 512) return 0;
    uint8_t *out = destination;
    unsigned followed = 0;
    while (remaining) {
        if (!baseos_logo_cluster(&f, file_cluster)) return 0;
        uint32_t first = file_cluster, run = 0, next;
        for (;;) {
            if (++followed > 8192 || !baseos_logo_next(&f, file_cluster, &next)) return 0;
            uint32_t count = remaining - run;
            if (count > f.spc) count = f.spc;
            run += count;
            if (run == remaining) { if (next < f.eoc) return 0; break; }
            if (!baseos_logo_cluster(&f, next)) return 0;
            if (next != file_cluster + 1) break;
            file_cluster = next;
        }
        if (!baseos_logo_read(&f, f.data + (first - 2) * f.spc, run, out)) return 0;
        remaining -= run; out += run * 512; file_cluster = next;
    }
    *bytes = file_bytes;
    return 1;
}
#endif
