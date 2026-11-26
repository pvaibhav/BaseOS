#ifndef BASEOS_LZ4_STREAM_H
#define BASEOS_LZ4_STREAM_H

/* Resumable decoder for the BOL4 kernel framing: "BOL4", packed bytes, exact
 * unpacked bytes, FNV-1a of the packed bytes, then one raw LZ4 block. */
struct baseos_lz4 {
    const unsigned char *p, *end, *hashed;
    unsigned char *dst, *out, *limit;
    unsigned int hash, expected;
};

/* 0 when the header fits the output capacity and the input extent; negative otherwise. */
int baseos_lz4_begin(struct baseos_lz4 *state, unsigned char *dst, unsigned long capacity,
                     const unsigned char *src, unsigned long available);

/* Do one bounded slice of work: 1 while more remains, 0 when exactly the declared
 * output was produced, negative on a hash or stream error. Never writes outside
 * [dst, dst + unpacked bytes) and never reads outside the packed extent. */
int baseos_lz4_step(struct baseos_lz4 *state);

#endif
