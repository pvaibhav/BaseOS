#ifndef BASEOS_SEQUENTIAL_BOOT_BLOB_H
#define BASEOS_SEQUENTIAL_BOOT_BLOB_H

#include <stdint.h>
#include <stddef.h>

#define BASEOS_SEQ_MAGIC "BOSSEQ1\0"
#define BASEOS_SEQ_VERSION 1u
#define BASEOS_SEQ_HEADER_BYTES 512u
#define BASEOS_SEQ_SLOT_BYTES 0x20000000ull
#define BASEOS_SEQ_TAIL_BYTES 0x02800000ull
#define BASEOS_SEQ_TAIL_OFFSET (BASEOS_SEQ_SLOT_BYTES - BASEOS_SEQ_TAIL_BYTES)
#define BASEOS_SEQ_INITRD_CAPACITY 0x02000000u
#define BASEOS_SEQ_IMAGE_CAPACITY 0x03000000u

/* Little-endian media data. native_abi identifies the model/kernel firmware ABI;
 * root_build_id identifies mutable slot contents and is checked by /init. */
struct baseos_seq_header {
    unsigned char magic[8];
    uint32_t version, header_bytes, model_id, image_bytes, image_fnv;
    uint32_t initrd_bytes, initrd_fnv, library_bytes, library_fnv, header_fnv;
    uint64_t slot_bytes, tail_offset;
    unsigned char native_abi[32], root_build_id[32];
    unsigned char image_sha256[32], kernel_sha256[32];
    unsigned char initrd_sha256[32], library_sha256[32];
    unsigned char reserved[256];
};
_Static_assert(sizeof(struct baseos_seq_header) == 512, "sequential header size");
_Static_assert(offsetof(struct baseos_seq_header, header_fnv) == 44, "header checksum offset");
_Static_assert(offsetof(struct baseos_seq_header, slot_bytes) == 48, "slot extent offset");
_Static_assert(offsetof(struct baseos_seq_header, native_abi) == 64, "firmware ABI offset");
_Static_assert(offsetof(struct baseos_seq_header, root_build_id) == 96, "root build identity offset");
_Static_assert(offsetof(struct baseos_seq_header, reserved) == 256, "reserved data offset");

#endif
