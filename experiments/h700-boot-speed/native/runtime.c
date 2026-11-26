#include <stddef.h>
#include <stdint.h>

void *memcpy(void *destination, const void *source, size_t bytes)
{
    unsigned char *out = destination;
    const unsigned char *in = source;
    while (bytes--) *out++ = *in++;
    return destination;
}

void *memmove(void *destination, const void *source, size_t bytes)
{
    unsigned char *out = destination;
    const unsigned char *in = source;
    if (out > in && out < in + bytes) {
        out += bytes;
        in += bytes;
        while (bytes--) *--out = *--in;
    } else memcpy(out, in, bytes);
    return destination;
}

void *memset(void *destination, int value, size_t bytes)
{
    unsigned char *out = destination;
    while (bytes--) *out++ = value;
    return destination;
}

int memcmp(const void *left, const void *right, size_t bytes)
{
    const unsigned char *a = left, *b = right;
    while (bytes--) { if (*a != *b) return *a - *b; a++; b++; }
    return 0;
}

void *memchr(const void *source, int value, size_t bytes)
{
    const unsigned char *in = source;
    while (bytes--) { if (*in == (unsigned char)value) return (void *)in; in++; }
    return NULL;
}

size_t strlen(const char *s) { const char *p = s; while (*p) p++; return p - s; }
size_t strnlen(const char *s, size_t n) { size_t i = 0; while (i < n && s[i]) i++; return i; }
int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}
int strncmp(const char *a, const char *b, size_t n)
{
    while (n && *a && *a == *b) { a++; b++; n--; }
    return n ? (unsigned char)*a - (unsigned char)*b : 0;
}
char *strchr(const char *s, int c)
{
    do { if (*s == c) return (char *)s; } while (*s++);
    return NULL;
}
char *strrchr(const char *s, int c)
{
    const char *found = NULL;
    do { if (*s == c) found = s; } while (*s++);
    return (char *)found;
}
