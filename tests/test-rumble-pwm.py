#!/usr/bin/env python3
"""Run the production H700 rumble PWM functions against a host stand-in."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'src/h700-rumble-pwm/h700_rumble_pwm.c').read_text()
assert 'module_exit' not in source


def function(name):
    start = source.rindex('\nstatic ', 0, source.index(name + '(')) + 1
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


header = r'''
#include <assert.h>
#include <ctype.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define NSEC_PER_MSEC 1000000ULL
#define PAGE_SIZE 4096
#define HRTIMER_MODE_REL 0
#define RUMBLE_DEFAULT_MAX_DUTY_PCT 60
#define RUMBLE_DEFAULT_MIN_DUTY_PCT 40
#define RUMBLE_PERIOD_NS            (20ULL * NSEC_PER_MSEC)
#define RUMBLE_MIN_PULSE_NS         (1ULL * NSEC_PER_MSEC)
#define RUMBLE_FF_MAX_MAGNITUDE     0xFFFFU
#define FF_RUMBLE   0x50
#define FF_PERIODIC 0x51
#define FF_CONSTANT 0x52
#define clamp(v, lo, hi) ((v) < (lo) ? (lo) : ((v) > (hi) ? (hi) : (v)))
#define clamp_t(t, v, lo, hi) ((t)clamp((t)(v), (t)(lo), (t)(hi)))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
enum hrtimer_restart { HRTIMER_NORESTART = 0, HRTIMER_RESTART = 1 };
struct device { int dummy; };
struct device_attribute { int dummy; };
struct power_supply { struct device dev; };
struct hrtimer { u64 next_ns; };
struct work_struct { int dummy; };
struct ff_effect {
    uint16_t type;
    union {
        struct { uint16_t strong_magnitude, weak_magnitude; } rumble;
        struct { int16_t level; } constant;
        struct { int16_t magnitude; } periodic;
    } u;
};
static int max_duty_pct = RUMBLE_DEFAULT_MAX_DUTY_PCT;
static int min_duty_pct = RUMBLE_DEFAULT_MIN_DUTY_PCT;
static struct power_supply fake_psy, *rumble_psy = &fake_psy;
static struct device_attribute fake_attr, *orig_dev_attr = &fake_attr;
static int spin_locked, hw_locked, state_lock, hw_mutex, system_highpri_wq;
#define spin_lock_irqsave(l, f) do { (void)(l); (f) = 0; assert(!spin_locked); spin_locked = 1; } while (0)
#define spin_unlock_irqrestore(l, f) do { (void)(l); (void)(f); assert(spin_locked); spin_locked = 0; } while (0)
#define mutex_lock(m) do { (void)(m); assert(!spin_locked && !hw_locked); hw_locked = 1; } while (0)
#define mutex_unlock(m) do { (void)(m); assert(hw_locked); hw_locked = 0; } while (0)
static struct hrtimer pwm_timer;
static struct work_struct rumble_work;
static bool pwm_active, phase_on;
static int target_duty_pct, wanted_hw, current_hw = -1, hw_pin = -1, store_fail_next, timer_starts;
static u64 pwm_on_ns, pwm_off_ns;
static ssize_t mock_orig_store(struct device *d, struct device_attribute *a, const char *buf, size_t n) {
    assert(hw_locked && !spin_locked && n == 2);
    if (store_fail_next) { store_fail_next = 0; return -EIO; }
    hw_pin = (buf[0] == '1') ? 1 : 0;
    return (ssize_t)n;
}
static ssize_t (*orig_store)(struct device *, struct device_attribute *, const char *, size_t) = mock_orig_store;
static u64 div_u64(u64 n, unsigned int d) { return n / d; }
static u64 ns_to_ktime(u64 ns) { return ns; }
static void hrtimer_forward_now(struct hrtimer *t, u64 ns) { t->next_ns = ns; }
static void hrtimer_start(struct hrtimer *t, u64 ns, int m) { assert(spin_locked); t->next_ns = ns; timer_starts++; }
static int hrtimer_try_to_cancel(struct hrtimer *t) { assert(spin_locked); return 0; }
static int hrtimer_cancel(struct hrtimer *t) { assert(!spin_locked); return 0; }
static bool queue_work(int wq, struct work_struct *w) { return true; }
static bool cancel_work_sync(struct work_struct *w) { assert(!spin_locked); return false; }
static int scnprintf(char *b, size_t s, const char *f, int v) { return snprintf(b, s, f, v); }
static int kstrtoint(const char *s, unsigned int base, int *res) {
    char *end; errno = 0;
    while (isspace((unsigned char)*s)) s++;
    if (!*s) return -EINVAL;
    long v = strtol(s, &end, (int)base);
    if (end == s || (*end != '\0' && *end != '\n') || errno == ERANGE) return -EINVAL;
    *res = (int)v; return 0;
}
'''

body = '\n'.join(function(n) for n in [
    'effective_min_duty', 'effective_max_duty', 'scale_ff_effect',
    'rumble_compute_pulses', 'rumble_write_hw', 'rumble_work_fn',
    'rumble_timer_fn', 'rumble_set_duty', 'rumble_stop_sync',
    'rumble_moto_show', 'rumble_moto_store',
])

main = r'''
int main(void) {
    char out[16];
    struct ff_effect eff = { .type = FF_RUMBLE };
    assert(rumble_moto_show(&fake_psy.dev, &fake_attr, out) == 2 && strcmp(out, "0\n") == 0);
    assert(rumble_moto_store(&fake_psy.dev, &fake_attr, "1\n", 2) == 2);
    rumble_work_fn(&rumble_work);
    assert(pwm_active && hw_pin == 1 && target_duty_pct == 60 && timer_starts == 1);
    assert(pwm_on_ns == 12000000ULL && pwm_off_ns == 8000000ULL);
    assert(rumble_timer_fn(&pwm_timer) == HRTIMER_RESTART);
    rumble_work_fn(&rumble_work);
    assert(!phase_on && hw_pin == 0 && rumble_moto_show(&fake_psy.dev, &fake_attr, out) == 2 && strcmp(out, "1\n") == 0);

    eff.u.rumble.strong_magnitude = 4681;
    rumble_set_duty(scale_ff_effect(&eff));
    assert(timer_starts == 1 && target_duty_pct == 41 && pwm_on_ns == 8200000ULL);
    min_duty_pct = 30;
    assert(scale_ff_effect(&eff) == 32);
    min_duty_pct = 40;
    eff.u.rumble.strong_magnitude = 0xFFFF;
    assert(scale_ff_effect(&eff) == 60);
    eff.u.rumble.strong_magnitude = 0;
    assert(scale_ff_effect(&eff) == 0);

    assert(rumble_timer_fn(&pwm_timer) == HRTIMER_RESTART);
    rumble_work_fn(&rumble_work);
    store_fail_next = 1; rumble_stop_sync();
    assert(hw_pin == 1 && current_hw == 1);
    rumble_stop_sync();
    assert(!pwm_active && hw_pin == 0 && current_hw == 0);

    max_duty_pct = 100; timer_starts = 0;
    assert(rumble_moto_store(&fake_psy.dev, &fake_attr, "1\n", 2) == 2);
    rumble_work_fn(&rumble_work);
    assert(!pwm_active && hw_pin == 1 && timer_starts == 0);
    max_duty_pct = 1; assert(effective_max_duty() == 40);
    max_duty_pct = 0; assert(effective_max_duty() == 0 && scale_ff_effect(&eff) == 0);
    assert(rumble_moto_store(&fake_psy.dev, &fake_attr, "bad\n", 4) == -EINVAL);
    puts("rumble PWM tests passed");
}
'''

cc = os.environ.get('CC', 'cc')
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp)
    (path / 'test.c').write_text(header + body + main)
    subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Wno-unused-parameter', '-Werror',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
