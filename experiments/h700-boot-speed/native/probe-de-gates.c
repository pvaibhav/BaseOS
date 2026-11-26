/* Bounded lab diagnostic. Match the running native generation before enabling
 * only the three zero DE0 gates in the exact kernel helper's order. */
#define _POSIX_C_SOURCE 200809L
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    if (argc != 2 || strlen(argv[1]) != 64) return 2;
    unsigned char generation[33];
    int fd = open("/sys/firmware/devicetree/base/chosen/baseos,native-boot", O_RDONLY);
    if (fd < 0) { perror("native generation"); return 1; }
    ssize_t count = read(fd, generation, sizeof(generation));
    close(fd);
    if (count != 32) return 3;
    char encoded[65];
    for (unsigned i = 0; i < 32; i++) snprintf(encoded + i * 2, 3, "%02x", generation[i]);
    if (strcmp(encoded, argv[1])) return 3;
    fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }
    volatile uint32_t *top = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0x01008000);
    close(fd);
    if (top == MAP_FAILED) { perror("mmap"); return 1; }
    printf("before=%08x,%08x,%08x\n", top[0], top[1], top[2]);
    if (top[0] || top[1] || top[2]) { munmap((void *)top, 4096); return 4; }
    top[0] = top[0] | 1;
    top[1] = top[1] | 1;
    top[2] = 1;
    __asm__ volatile("dsb sy" ::: "memory");
    printf("after=%08x,%08x,%08x\n", top[0], top[1], top[2]);
    int valid = top[0] == 1 && top[1] == 1 && top[2] == 1;
    munmap((void *)top, 4096);
    return valid ? 0 : 5;
}
