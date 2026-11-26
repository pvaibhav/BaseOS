#include <stdlib.h>

__attribute__((visibility("hidden"))) char __libc_single_threaded = 0;

__attribute__((visibility("hidden")))
unsigned long __isoc23_strtoul(const char *s, char **end, int base)
{
    return strtoul(s, end, base);
}
