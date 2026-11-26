/* Bounded LZ4 adapter for the vendor gunzip ABI. No allocation or hardware IO. */
typedef unsigned char u8;
typedef unsigned int u32;
typedef __SIZE_TYPE__ usize;

static u32 little32(const u8 *p)
{
    return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

/* Framing: BOL4, packed bytes, exact unpacked bytes, FNV-1a of packed bytes.
 * The enclosing Android image supplies the available input extent. */
int custom_unpack(u8 *dst, unsigned long capacity, const u8 *src,
                  unsigned long *length)
{
    usize available = *length;
    if (available < 16 || little32(src) != 0x344c4f42)
        return -1;
    usize packed = little32(src + 4), wanted = little32(src + 8);
    if (!wanted || wanted > capacity || packed != available - 16)
        return -2;
    const u8 *p = src + 16, *end = p + packed;
    u32 hash = 2166136261u;
    for (const u8 *q = p; q < end; ++q)
        hash = (hash ^ *q) * 16777619u;
    if (hash != little32(src + 12))
        return -3;
    u8 *out = dst, *limit = dst + wanted;
    while (p < end) {
        unsigned token = *p++;
        usize literal = token >> 4, match = (token & 15) + 4;
        if (literal == 15) {
            unsigned extra;
            do {
                if (p == end) return -4;
                extra = *p++;
                if (literal > wanted || extra > wanted - literal) return -5;
                literal += extra;
            } while (extra == 255);
        }
        if (literal > (usize)(end - p) || literal > (usize)(limit - out))
            return -6;
        while (literal--) *out++ = *p++;
        if (p == end) {
            if (out != limit) return -7;
            *length = wanted;
            return 0;
        }
        if (end - p < 2) return -8;
        usize offset = p[0] | (unsigned)p[1] << 8;
        p += 2;
        if (!offset || offset > (usize)(out - dst)) return -9;
        if ((token & 15) == 15) {
            unsigned extra;
            do {
                if (p == end) return -10;
                extra = *p++;
                if (match > wanted || extra > wanted - match) return -11;
                match += extra;
            } while (extra == 255);
        }
        if (match > (usize)(limit - out)) return -12;
        const u8 *from = out - offset;
        while (match && ((usize)out & 3)) {
            *out++ = *from++;
            --match;
        }
        if (offset == 1) {
            u32 repeated = (u32)*from * 0x01010101u;
            while (match >= 4) {
                *(u32 *)out = repeated;
                out += 4;
                from += 4;
                match -= 4;
            }
        } else if (offset >= 4 && !((usize)from & 3)) {
            while (match >= 4) {
                *(u32 *)out = *(const u32 *)from;
                out += 4;
                from += 4;
                match -= 4;
            }
        }
        while (match--) *out++ = *from++;
    }
    return -13;
}
