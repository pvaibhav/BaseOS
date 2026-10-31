/* mem ADDR [VALUE]: read or write one 32-bit physical register. */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
int main(int argc, char **argv) {
 if (argc < 2 || argc > 3) { fprintf(stderr, "usage: mem ADDR [VALUE]\n"); return 2; }
 unsigned long addr = strtoul(argv[1], 0, 0), page = addr & ~0xfffUL;
 int fd = open("/dev/mem", (argc == 3 ? O_RDWR : O_RDONLY) | O_SYNC);
 if (fd < 0) { perror("/dev/mem"); return 1; }
 volatile uint32_t *p = mmap(0, 4096, PROT_READ | (argc == 3 ? PROT_WRITE : 0), MAP_SHARED, fd, page);
 if (p == MAP_FAILED) { perror("mmap"); return 1; }
 p += (addr - page) / 4;
 if (argc == 3) *p = (uint32_t)strtoul(argv[2], 0, 0);
 printf("0x%08x\n", *p);
 return 0;
}
