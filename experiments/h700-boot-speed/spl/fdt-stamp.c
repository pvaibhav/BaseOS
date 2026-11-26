/* Add an inert /chosen property that survives libfdt packing in vendor U-Boot. */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;

static u32 get32(const u8 *p)
{
    return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3];
}

static void put32(u8 *p, u32 value)
{
    volatile u8 *out = p;
    out[0] = value >> 24; out[1] = value >> 16; out[2] = value >> 8; out[3] = value;
}

static void copy_bytes(u8 *destination, const u8 *source, u32 bytes)
{
    volatile u8 *out = destination;
    for (u32 i = 0; i < bytes; i++) out[i] = source[i];
}

static int named(const u8 *p, const char *name, u32 limit)
{
    for (u32 i = 0; i < limit; i++) {
        if (p[i] != (u8)name[i]) return 0;
        if (!name[i]) return 1;
    }
    return 0;
}

int baseos_stamp_fdt(void *blob, const u64 data[4])
{
    u8 *tree = blob;
    if (get32(tree) != 0xd00dfeed) return -1;
    u32 total = get32(tree + 4), structure = get32(tree + 8), strings = get32(tree + 12);
    u32 strings_size = get32(tree + 32), structure_size = get32(tree + 36);
    if (total > 0x40000 || strings > total || strings_size > total - strings ||
        structure > strings || structure_size > strings - structure) return -1;
    u32 end = structure + structure_size, pos = structure, insertion = 0;
    unsigned int depth = 0, chosen = 0, create_chosen = 0;
    while (pos + 4 <= end) {
        u32 token = get32(tree + pos), at = pos;
        pos += 4;
        if (token == 1) {
            depth++;
            if (depth == 2 && named(tree + pos, "chosen", end - pos)) chosen = depth;
            while (pos < end && tree[pos]) pos++;
            if (pos == end) return -1;
            pos = (pos + 4) & ~3u;
        } else if (token == 2) {
            if (!depth) return -1;
            if (chosen == depth) { insertion = at; break; }
            if (depth == 1) { insertion = at; create_chosen = 1; break; }
            depth--;
        } else if (token == 3) {
            if (pos + 8 > end) return -1;
            u32 length = get32(tree + pos), name = get32(tree + pos + 4);
            pos += 8;
            if (length > end - pos || name >= strings_size) return -1;
            if (chosen == depth && chosen && named(tree + strings + name, "baseos,spl", strings_size - name)) {
                if (length != 32) return -1;
                copy_bytes(tree + pos, (const u8 *)data, 32);
                return 0;
            }
            pos = (pos + length + 3) & ~3u;
        } else if (token != 4) return -1;
    }
    if (!insertion) return -1;
    u32 extra = create_chosen ? 60 : 44, used = strings + strings_size;
    if (used > total || extra + 11 > total - used) return -1;
    volatile u8 *out = tree;
    for (u32 i = used; i > insertion; i--) out[i - 1 + extra] = tree[i - 1];
    pos = insertion;
    if (create_chosen) {
        put32(tree + pos, 1);
        copy_bytes(tree + pos + 4, (const u8 *)"chosen\0\0", 8);
        pos += 12;
    }
    put32(tree + pos, 3); put32(tree + pos + 4, 32); put32(tree + pos + 8, strings_size);
    copy_bytes(tree + pos + 12, (const u8 *)data, 32);
    if (create_chosen) put32(tree + pos + 44, 2);
    copy_bytes(tree + used + extra, (const u8 *)"baseos,spl", 11);
    put32(tree + 12, strings + extra);
    put32(tree + 32, strings_size + 11);
    put32(tree + 36, structure_size + extra);
    return 0;
}
