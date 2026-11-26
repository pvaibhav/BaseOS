/* Fixed trial slot in the unused upper half of the 64 MiB boot partition. */
extern unsigned int vendor_phyread(unsigned int sector, unsigned int count, void *destination);
#ifndef FIXED_TRIAL_SECTOR
#define FIXED_TRIAL_SECTOR 0x5a000
#endif
volatile unsigned int trial_sector __attribute__((visibility("hidden"))) = FIXED_TRIAL_SECTOR;
#ifdef QUICK_RECOVERY
extern int trial_persist(void);
#endif

int trial_read(void)
{
#ifdef QUICK_RECOVERY
    if (trial_persist()) return 1;
#endif
    unsigned int sector = trial_sector;
    unsigned int count = sector == 0x62000 ? TRIAL_FAST_SECTORS : TRIAL_GZIP_SECTORS;
    return vendor_phyread(sector, count, (void *)0x45000000) == count ? 0 : 1;
}
