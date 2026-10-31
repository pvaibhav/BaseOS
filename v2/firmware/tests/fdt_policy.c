/* Helper devicetree rule check: runs the variant's fdt-final.c (baseos_u2_fdt) on
 * the payload's real DTB, and on copies with a PMIC node or a broken compatible.
 * Built and run on the host by tests/run.py; prints one line per case:
 *   <case> <return value>   (0 = accepted, nonzero = refused)
 * With a second argument DIR it also writes the three input trees there
 * (payload.dtb, pmic.dtb, unterminated.dtb) for tests/bl31_dtb.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libfdt.h>
#include "helper.h"

static unsigned char src[0x100000], work[0x100000], dst[0x100000];

static const char *out_dir;

static void save(const char *name, const void *tree)
{
    char path[4096];
    if (!out_dir) return;
    snprintf(path, sizeof(path), "%s/%s.dtb", out_dir, name);
    FILE *f = fopen(path, "wb");
    if (!f || fwrite(tree, 1, fdt_totalsize(tree), f) != fdt_totalsize(tree) || fclose(f)) exit(2);
}

static int finalize(const char *name, const void *tree)
{
    save(name, tree);
    struct baseos_native_record r;
    struct baseos_u2_metadata m;
    memset(&r, 0, sizeof(r));
    memset(&m, 0, sizeof(m));
    r.ram_bytes = 0x40000000ull;
    r.boot_reason = BASEOS_BOOT_UNKNOWN;
    strcpy(m.serial, "0c00105562458931c52");
    static unsigned char display[80];
    m.display = display;
    m.display_bytes = sizeof(display);
    m.logo_address = 0x7bf20000ull;
    memset(dst, 0, sizeof(dst));
    return baseos_u2_fdt(dst, tree, fdt_totalsize(tree), &r, 0x100000, &m);
}

/* Copy src into work with room, then add /pmic@34 with the given compatible bytes. */
static const void *with_node(const char *compatible, int bytes)
{
    if (fdt_open_into(src, work, sizeof(work)) ||
        fdt_add_subnode(work, 0, "pmic@34") < 0 ||
        fdt_setprop(work, fdt_path_offset(work, "/pmic@34"), "compatible", compatible, bytes) ||
        fdt_pack(work)) {
        fprintf(stderr, "fixture edit failed\n");
        exit(2);
    }
    return work;
}

int main(int argc, char **argv)
{
    FILE *f = argc >= 2 ? fopen(argv[1], "rb") : NULL;
    out_dir = argc == 3 ? argv[2] : NULL;
    if (!f || fread(src, 1, sizeof(src), f) < 40 || fdt_check_header(src)) {
        fprintf(stderr, "usage: fdt_policy DTB [DIR]\n");
        return 2;
    }
    static const char pmic[] = "x-powers,axp717";
    static const char unterminated[] = {'x', '-', 'p', 'o', 'w', 'e', 'r', 's'};
    printf("payload %d\n", finalize("payload", src));
    printf("pmic %d\n", finalize("pmic", with_node(pmic, sizeof(pmic))));
    printf("unterminated %d\n", finalize("unterminated", with_node(unterminated, sizeof(unterminated))));
    return 0;
}
