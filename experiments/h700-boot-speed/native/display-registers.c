/* Read-only capture of display clock, pin and IOMMU state for stock comparison. */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

int main(void)
{
    const unsigned long pages[] = {0x03001000, 0x0300b000, 0x030f0000,
                                  0x01008000, 0x01011000, 0x01281000, 0x011c1000,
                                  0x06510000, 0x06511000};
    int fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }
    for (unsigned i = 0; i < sizeof(pages) / sizeof(pages[0]); i++) {
        volatile const uint32_t *p = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, pages[i]);
        if (p == MAP_FAILED) { perror("mmap"); close(fd); return 1; }
        for (unsigned j = 0; j < 4096 / 4; j++)
            printf("%08lx %08x\n", pages[i] + j * 4, p[j]);
        if (munmap((void *)p, 4096)) { perror("munmap"); close(fd); return 1; }
    }
    close(fd);
    return 0;
}
