#ifndef BASEOS_ATTEMPT_H
#define BASEOS_ATTEMPT_H
#include <stddef.h>
#include <stdint.h>
#ifndef UINT32_C
#define UINT32_C(value) value##U
#endif
#include <string.h>

/* Provisional exclusive ownership: RTC GP14/GP15 only. */
#define ATTEMPT_PENDING UINT32_C(0x42535031) /* "BSP1", fixed protocol version */
enum attempt_word { ATTEMPT_COOKIE, ATTEMPT_MARKER };
struct attempt_pair { uint32_t cookie, marker; };
enum boot_action { STOCK_RESUME, NATIVE_ATTEMPT, STOCK_RECOVERY, INVALID_GENERATION };
enum ack_action { ACK_REFUSED, ACK_CLEAR, ACK_ALREADY_CLEAR };
enum io_result { IO_REFUSED, IO_COMPLETE, IO_ALREADY_CLEAR, IO_CHANGED };
struct attempt_io {
    void *context;
    uint32_t (*read)(void *, enum attempt_word);
    void (*write)(void *, enum attempt_word, uint32_t);
    void (*fence)(void *);
};

static inline uint32_t generation_cookie(const uint8_t generation[32])
{
    uint32_t value = UINT32_C(2166136261);
    for (size_t i = 0; i < 32; i++) value = (value ^ generation[i]) * UINT32_C(16777619);
    return value;
}

static inline int pair_equal(struct attempt_pair a, struct attempt_pair b)
{
    return a.cookie == b.cookie && a.marker == b.marker;
}

static inline enum boot_action boot_plan(struct attempt_pair pair, int vendor_resume,
                                          uint32_t cookie)
{
    if (vendor_resume) return STOCK_RESUME;
    if (pair.cookie || pair.marker) return STOCK_RECOVERY;
    /* Zero defeats the first durable cookie write. Reject at build/install. */
    return cookie ? NATIVE_ATTEMPT : INVALID_GENERATION;
}

static inline enum ack_action ack_plan(struct attempt_pair pair, int health_passed,
                                       const uint8_t running[32], const uint8_t installed[32])
{
    uint32_t cookie = generation_cookie(installed);
    if (!health_passed || !cookie || memcmp(running, installed, 32)) return ACK_REFUSED;
    if (!pair.cookie && !pair.marker) return ACK_ALREADY_CLEAR;
    return pair.cookie == cookie && pair.marker == ATTEMPT_PENDING ? ACK_CLEAR : ACK_REFUSED;
}

static inline struct attempt_pair pair_read(struct attempt_io *io)
{
    struct attempt_pair pair;
    pair.cookie = io->read(io->context, ATTEMPT_COOKIE);
    pair.marker = io->read(io->context, ATTEMPT_MARKER);
    return pair;
}

/* The caller has already selected no-resume, and owns the entire boot phase.
 * Native DRAM/load/handoff may start ONLY after IO_COMPLETE. */
static inline enum io_result attempt_commit(struct attempt_io *io, uint32_t cookie)
{
    const struct attempt_pair empty = {0, 0}, staged = {cookie, 0};
    const struct attempt_pair pending = {cookie, ATTEMPT_PENDING};
    if (!cookie || !pair_equal(pair_read(io), empty)) return IO_REFUSED;
    io->fence(io->context);
    if (!pair_equal(pair_read(io), empty)) return IO_CHANGED;
    io->write(io->context, ATTEMPT_COOKIE, cookie);
    io->fence(io->context);
    if (!pair_equal(pair_read(io), staged)) return IO_CHANGED;
    io->write(io->context, ATTEMPT_MARKER, ATTEMPT_PENDING);
    io->fence(io->context);
    return pair_equal(pair_read(io), pending) ? IO_COMPLETE : IO_CHANGED;
}

/* MMIO has no atomic pair CAS: comparisons detect observed changes. Exclusive
 * firmware ownership plus one serialized Linux health supervisor is required. */
static inline enum io_result attempt_ack(struct attempt_io *io, int health_passed,
                                         const uint8_t running[32], const uint8_t installed[32])
{
    if (!health_passed || !generation_cookie(installed) || memcmp(running, installed, 32))
        return IO_REFUSED;
    struct attempt_pair pair = pair_read(io);
    enum ack_action action = ack_plan(pair, health_passed, running, installed);
    if (action == ACK_REFUSED) return IO_REFUSED;
    if (action == ACK_ALREADY_CLEAR) return IO_ALREADY_CLEAR;
    io->fence(io->context);
    if (!pair_equal(pair_read(io), pair)) return IO_CHANGED;
    /* Keep the pending marker until the final write. Torn clear => recovery. */
    io->write(io->context, ATTEMPT_COOKIE, 0);
    io->fence(io->context);
    const struct attempt_pair clearing = {0, ATTEMPT_PENDING}, empty = {0, 0};
    if (!pair_equal(pair_read(io), clearing)) return IO_CHANGED;
    io->write(io->context, ATTEMPT_MARKER, 0);
    io->fence(io->context);
    return pair_equal(pair_read(io), empty) ? IO_COMPLETE : IO_CHANGED;
}
#endif
