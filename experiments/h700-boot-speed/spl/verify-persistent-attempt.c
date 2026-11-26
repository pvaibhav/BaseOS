#include "persistent-attempt.h"
static const uint8_t zero_generation[32] = {0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,235,126,213,155};
static const uint8_t collision_a[32] = {51,220,138,33,20,40,7,162,30,111,157,197,174,134,27,55,169,245,83,55,112,92,10,232,100,149,235,63,156,13,133,19};
static const uint8_t collision_b[32] = {74,182,81,115,117,35,49,238,99,152,42,204,195,108,209,183,211,253,18,153,87,110,90,135,242,200,175,231,206,148,237,203};

#include <assert.h>
#include <setjmp.h>
#include <stdio.h>

struct fake_rtc {
    uint32_t words[128];
    unsigned steps, writes, stop_after, change_before, drop_write;
    struct attempt_pair foreign;
    char kinds[64];
    struct attempt_pair snapshots[64];
};
static struct fake_rtc state;
static jmp_buf power_loss;
static const unsigned owned[2] = {0x138 / 4, 0x13c / 4};

static struct attempt_pair snapshot(void)
{
    struct attempt_pair p = {state.words[owned[0]], state.words[owned[1]]};
    return p;
}
static void before(void)
{
    if (state.change_before && state.steps + 1 == state.change_before) {
        state.words[owned[0]] = state.foreign.cookie;
        state.words[owned[1]] = state.foreign.marker;
    }
}
static void after(char kind)
{
    state.kinds[state.steps] = kind;
    state.snapshots[state.steps++] = snapshot();
    if (state.stop_after && state.steps == state.stop_after) longjmp(power_loss, 1);
}
static uint32_t fake_read(void *context, enum attempt_word word)
{
    assert(context == &state && word <= ATTEMPT_MARKER);
    before();
    uint32_t value = state.words[owned[word]];
    after('r');
    return value;
}
static void fake_write(void *context, enum attempt_word word, uint32_t value)
{
    assert(context == &state && word <= ATTEMPT_MARKER);
    before();
    state.writes++;
    if (state.writes != state.drop_write) state.words[owned[word]] = value;
    after(word == ATTEMPT_COOKIE ? 'c' : 'm');
}
static void fake_barrier(void *context)
{
    assert(context == &state);
    before();
    after('b');
}
static struct attempt_io io = {&state, fake_read, fake_write, fake_barrier};
static void initialize(struct attempt_pair pair)
{
    memset(&state, 0, sizeof(state));
    for (unsigned i = 0; i < 128; i++) state.words[i] = 0x70000000u + i;
    state.words[owned[0]] = pair.cookie;
    state.words[owned[1]] = pair.marker;
}
static void check_vendor_words(void)
{
    for (unsigned i = 0; i < 128; i++)
        if (i != owned[0] && i != owned[1]) assert(state.words[i] == 0x70000000u + i);
}
static const char *action_name(enum boot_action action)
{
    return action == NATIVE_ATTEMPT ? "native" : action == STOCK_RECOVERY ? "stock-recovery" :
           action == STOCK_RESUME ? "stock-resume" : "invalid-generation";
}

int main(void)
{
    const uint32_t cookie = generation_cookie(collision_a);
    const struct attempt_pair empty = {0, 0}, pending = {cookie, ATTEMPT_PENDING};
    assert(cookie && generation_cookie(collision_b) == cookie);
    assert(memcmp(collision_a, collision_b, 32));
    assert(generation_cookie(zero_generation) == 0);
    initialize(empty);
    assert(attempt_commit(&io, 0) == IO_REFUSED && !state.steps);
    uint32_t values[] = {0, 1, cookie, ATTEMPT_PENDING, 0xffffffffu, cookie ^ 1};
    unsigned decision_cases = 0;
    for (unsigned i = 0; i < sizeof(values)/sizeof(*values); i++)
    for (unsigned j = 0; j < sizeof(values)/sizeof(*values); j++) {
        struct attempt_pair p = {values[i], values[j]};
        assert(boot_plan(p, 1, cookie) == STOCK_RESUME);
        assert(boot_plan(p, 1, 0) == STOCK_RESUME);
        assert(boot_plan(p, 0, cookie) == (i || j ? STOCK_RECOVERY : NATIVE_ATTEMPT));
        assert(boot_plan(p, 0, 0) == (i || j ? STOCK_RECOVERY : INVALID_GENERATION));
        enum ack_action expected_ack = !p.cookie && !p.marker ? ACK_ALREADY_CLEAR :
            p.cookie == cookie && p.marker == ATTEMPT_PENDING ? ACK_CLEAR : ACK_REFUSED;
        assert(ack_plan(p, 1, collision_a, collision_a) == expected_ack);
        initialize(p);
        enum io_result ack_result = attempt_ack(&io, 1, collision_a, collision_a);
        assert(ack_result == (expected_ack == ACK_CLEAR ? IO_COMPLETE :
                             expected_ack == ACK_ALREADY_CLEAR ? IO_ALREADY_CLEAR : IO_REFUSED));
        if (expected_ack != ACK_CLEAR) assert(!state.writes);
        check_vendor_words();
        decision_cases += 4;
    }
    assert(ack_plan(pending, 1, collision_b, collision_a) == ACK_REFUSED);
    assert(ack_plan(pending, 0, collision_a, collision_a) == ACK_REFUSED);
    assert(ack_plan(pending, 1, zero_generation, zero_generation) == ACK_REFUSED);
    initialize(pending);
    assert(attempt_ack(&io, 1, collision_b, collision_a) == IO_REFUSED && !state.steps);
    assert(attempt_ack(&io, 0, collision_a, collision_a) == IO_REFUSED && !state.steps);

    initialize(empty);
    assert(attempt_commit(&io, cookie) == IO_COMPLETE);
    assert(pair_equal(snapshot(), pending));
    assert(attempt_ack(&io, 1, collision_a, collision_a) == IO_COMPLETE);
    assert(pair_equal(snapshot(), empty) && state.writes == 4);
    assert(attempt_ack(&io, 1, collision_a, collision_a) == IO_ALREADY_CLEAR);
    assert(state.writes == 4);
    check_vendor_words();

    printf("{\n  \"decision_cases\": %u,\n  \"collision_cookie\": \"%08x\",\n", decision_cases, cookie);
    printf("  \"commit_power_loss\": [\n");
    initialize(empty); assert(attempt_commit(&io, cookie) == IO_COMPLETE);
    unsigned commit_steps = state.steps;
    for (unsigned cut = 0; cut <= commit_steps; cut++) {
        initialize(empty); state.stop_after = cut;
        if (cut && !setjmp(power_loss)) (void)attempt_commit(&io, cookie);
        struct attempt_pair p = snapshot();
        enum boot_action action = boot_plan(p, 0, cookie);
        assert(action == (p.cookie || p.marker ? STOCK_RECOVERY : NATIVE_ATTEMPT));
        /* No call to native DRAM is permitted until commit returns complete. */
        check_vendor_words();
        printf("    {\"after_io\":%u,\"gp14\":\"%08x\",\"gp15\":\"%08x\",\"restart\":\"%s\"}%s\n",
               cut, p.cookie, p.marker, action_name(action), cut == commit_steps ? "" : ",");
    }
    printf("  ],\n  \"ack_power_loss\": [\n");
    initialize(pending); assert(attempt_ack(&io, 1, collision_a, collision_a) == IO_COMPLETE);
    unsigned ack_steps = state.steps;
    for (unsigned cut = 0; cut <= ack_steps; cut++) {
        initialize(pending); state.stop_after = cut;
        if (cut && !setjmp(power_loss)) (void)attempt_ack(&io, 1, collision_a, collision_a);
        struct attempt_pair p = snapshot();
        enum boot_action action = boot_plan(p, 0, cookie);
        assert(action == (p.cookie || p.marker ? STOCK_RECOVERY : NATIVE_ATTEMPT));
        if (!p.cookie && p.marker) assert(p.marker == ATTEMPT_PENDING);
        check_vendor_words();
        printf("    {\"after_io\":%u,\"gp14\":\"%08x\",\"gp15\":\"%08x\",\"restart\":\"%s\"}%s\n",
               cut, p.cookie, p.marker, action_name(action), cut == ack_steps ? "" : ",");
    }
    unsigned dropped = 0;
    for (unsigned write = 1; write <= 2; write++) {
        initialize(empty); state.drop_write = write;
        assert(attempt_commit(&io, cookie) == IO_CHANGED);
        check_vendor_words(); dropped++;
        initialize(pending); state.drop_write = write;
        assert(attempt_ack(&io, 1, collision_a, collision_a) == IO_CHANGED);
        assert(boot_plan(snapshot(), 0, cookie) == STOCK_RECOVERY);
        check_vendor_words(); dropped++;
    }
    /* An observed foreign pair at each guard is refused before the next write. */
    unsigned observed_changes = 0;
    for (unsigned event = 4; event <= 5; event++) {
        initialize(empty); state.change_before = event; state.foreign = (struct attempt_pair){0x11111111,0x22222222};
        assert(attempt_commit(&io, cookie) == IO_CHANGED && state.writes == 0); observed_changes++;
        initialize(pending); state.change_before = event; state.foreign = (struct attempt_pair){0x11111111,0x22222222};
        assert(attempt_ack(&io, 1, collision_a, collision_a) == IO_CHANGED && state.writes == 0); observed_changes++;
    }
    initialize(pending); state.change_before = 8; state.foreign = (struct attempt_pair){0x11111111,0x22222222};
    assert(attempt_ack(&io, 1, collision_a, collision_a) == IO_CHANGED && state.writes == 1); observed_changes++;
    /* Explicitly expose the non-atomic read/store window; do not claim MMIO CAS. */
    initialize(pending); state.change_before = 6; state.foreign = (struct attempt_pair){0x11111111,0x22222222};
    assert(attempt_ack(&io, 1, collision_a, collision_a) == IO_CHANGED);
    assert(snapshot().cookie == 0 && snapshot().marker == 0x22222222);
    printf("  ],\n  \"dropped_write_cases\": %u,\n  \"observed_change_guards\": %u,\n", dropped, observed_changes);
    printf("  \"foreign_writer_between_check_and_store_can_be_overwritten\": true,\n");
    printf("  \"full_generation_collision_ack_refused\": true,\n  \"zero_cookie_refused\": true,\n");
    printf("  \"vendor_rtc_words_untouched\": true,\n  \"scope\": \"shared RTC protocol; actual SPL behavior verified separately\",\n  \"passed\": true\n}\n");
    return 0;
}
