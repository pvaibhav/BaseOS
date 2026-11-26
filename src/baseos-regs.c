/* Read-only view of the few SoC registers baseos-diag reports.
 *
 * busybox has no devmem applet, and read() on /dev/mem refuses device memory
 * on arm64, so this maps each page read-only (PROT_READ, O_RDONLY) and prints
 * one fixed list. It takes no addresses: nothing outside the list can be read,
 * and nothing can be written. It takes no lock, so it never competes with
 * native-health for the RTC pair it reports.
 *
 * Output, one register per line: NAME ADDRESS VALUE, or NAME ADDRESS
 * unavailable when its page cannot be mapped. A register with a mask prints
 * only the masked bits: the chip id word keeps its low half, which picks the
 * DRAM wiring, and never shows the chip's serial number.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

#define PAGE_BYTES 4096u

static const struct {
    const char *name;
    uint32_t address, mask;
} registers[] = {
    {"rtc_gp0", 0x07000100, 0xffffffff},
    {"rtc_gp1", 0x07000104, 0xffffffff},
    {"rtc_gp2", 0x07000108, 0xffffffff},
    {"rtc_gp3", 0x0700010c, 0xffffffff},
    {"rtc_gp4", 0x07000110, 0xffffffff},
    {"rtc_gp5", 0x07000114, 0xffffffff},
    {"rtc_gp6", 0x07000118, 0xffffffff},
    {"rtc_gp7", 0x0700011c, 0xffffffff},
    {"rtc_gp8", 0x07000120, 0xffffffff},
    {"rtc_gp9", 0x07000124, 0xffffffff},
    {"rtc_gp10", 0x07000128, 0xffffffff},
    {"rtc_gp11", 0x0700012c, 0xffffffff},
    {"rtc_gp12", 0x07000130, 0xffffffff},
    {"rtc_gp13", 0x07000134, 0xffffffff},
    {"rtc_gp14", 0x07000138, 0xffffffff}, /* native attempt / stock latch: cookie */
    {"rtc_gp15", 0x0700013c, 0xffffffff}, /* native attempt / stock latch: marker */
    {"rtc_1f8", 0x070001f8, 0xffffffff},  /* bit 16: vendor resume, the capsule then starts stock */
    {"rtc_1fc", 0x070001fc, 0xffffffff},
    {"wdt_ctrl", 0x030090b0, 0xffffffff},
    {"wdt_cfg", 0x030090b4, 0xffffffff},
    {"wdt_mode", 0x030090b8, 0xffffffff},
    /* Chip id word 0: only its low half (the DRAM wiring choice) is read out. */
    {"sid0", 0x03006200, 0x0000ffff},
    {"pe_cfg0", 0x0300b090, 0xffffffff},
    {"pe_dat", 0x0300b0a0, 0xffffffff},
    {"pe_pull0", 0x0300b0ac, 0xffffffff},
    {"pi_cfg1", 0x0300b124, 0xffffffff},
    {"pi_dat", 0x0300b130, 0xffffffff},
    {"de_top0", 0x01008000, 0xffffffff},
    {"de_top4", 0x01008004, 0xffffffff},
    {"de_top8", 0x01008008, 0xffffffff},
    {"de_top100", 0x01008100, 0xffffffff},
    {"de_top108", 0x01008108, 0xffffffff},
    {"tcon_line", 0x065110fc, 0xffffffff},
};

int main(int argc, char **argv)
{
    (void)argv;
    if (argc != 1) {
        fprintf(stderr, "usage: baseos-regs\n");
        return 2;
    }
    int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    uint32_t mapped_base = 0;
    volatile uint32_t *page = MAP_FAILED;
    for (size_t i = 0; i < sizeof(registers) / sizeof(registers[0]); i++) {
        uint32_t address = registers[i].address, base = address & ~(PAGE_BYTES - 1);
        if (fd >= 0 && (page == MAP_FAILED || base != mapped_base)) {
            if (page != MAP_FAILED) munmap((void *)page, PAGE_BYTES);
            page = mmap(NULL, PAGE_BYTES, PROT_READ, MAP_SHARED, fd, base);
            mapped_base = base;
        }
        if (fd < 0 || page == MAP_FAILED)
            printf("%s 0x%08x unavailable\n", registers[i].name, (unsigned)address);
        else
            printf("%s 0x%08x 0x%08x\n", registers[i].name, (unsigned)address,
                   (unsigned)(page[(address - base) / 4] & registers[i].mask));
    }
    if (page != MAP_FAILED) munmap((void *)page, PAGE_BYTES);
    if (fd >= 0) close(fd);
    return fflush(stdout) == 0 ? 0 : 1;
}
