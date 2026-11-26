/* Persist only the verified sector that differs from normal boot. */
typedef unsigned char u8;
typedef unsigned int u32;
extern u32 vendor_phyread(u32 sector, u32 count, void *destination);
extern u32 vendor_phywrite(u32 sector, u32 count, const void *source);
#include "recovery-data.h"

int trial_persist(void)
{
    u8 storage[512 + 63];
    u8 *sector = (u8 *)(((u32)storage + 63) & ~63u);
    if (vendor_phyread(0x42000, 1, sector) != 1) return 1;
    u32 crc = (u32)sector[0] | (u32)sector[1] << 8 | (u32)sector[2] << 16 | (u32)sector[3] << 24;
    if (crc != CRC_NORMAL && crc != CRC_ARMED_NO && crc != CRC_ARMED_YES) return 1;
    for (u32 i = 0; i < 512; ++i) {
        u8 expected = restored_sector[i];
        if (crc != CRC_NORMAL) {
            if (i < 4) expected = crc >> (8 * i);
            else if (i >= BOOT_OFFSET && i < BOOT_OFFSET + 128)
                expected = i - BOOT_OFFSET < sizeof(ARMED_BOOT) - 1 ? ARMED_BOOT[i - BOOT_OFFSET] : ' ';
            else if (i >= FLAG_OFFSET && i < FLAG_OFFSET + 3)
                expected = (crc == CRC_ARMED_YES ? "yes" : "no ")[i - FLAG_OFFSET];
        }
        if (sector[i] != expected) return 1;
    }
    if (crc == CRC_NORMAL) return 0;
    for (u32 i = 0; i < 512; ++i) sector[i] = restored_sector[i];
    if (vendor_phywrite(0x42000, 1, sector) != 1) return 1;
    if (vendor_phyread(0x42000, 1, sector) != 1) return 1;
    for (u32 i = 0; i < 512; ++i)
        if (sector[i] != restored_sector[i]) return 1;
    return 0;
}
