/* Read only the three SID words used by the vendor serial-number routine. */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

int main(void)
{
    int fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }
    volatile uint32_t *sid = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0x03006000);
    close(fd);
    if (sid == MAP_FAILED) { perror("mmap"); return 1; }
    uint32_t high = sid[0x204 / 4], middle = sid[0x208 / 4], low = sid[0x20c / 4];
    printf("%08x %08x %08x\n%03x%08x%08x\n", high, middle, low, high >> 20, middle, low);
    munmap((void *)sid, 4096);
    return 0;
}
