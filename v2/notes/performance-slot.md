# Slot performance on the owner's RG SP

2026-11-01. Device `ac001089c89588720d2`. **Investigation remains open:** the
frontend change improves throughput, but the running kernel still starves audio.
Stock parity, battery parity and thermal parity have not been established.

## Changes

Slot's KMS backend used to queue a flip and wait for its vblank before returning
to the native frame pacer. It now retains both the current and pending GBM
buffers, waits for the previous flip at the next presentation, and releases the
old buffer only after receiving the completion event. EGL swap interval is zero
on GBM, where Slot owns scanout; stock fbdev retains interval one. No spinning,
additional threads or per-frame child processes are introduced. A swap return
now means submission, not physical presentation; response traces based on swap
returns must be interpreted accordingly.

The running BaseOS kernel has `CONFIG_HIGH_RES_TIMERS` disabled. `/proc/timer_list`
reports 4,000,000 ns resolution. `timer-probe` requests 100 one-millisecond sleeps:
minimum 3,815,719 ns, mean 3,998,827 ns, maximum 4,184,786 ns. This introduces
millisecond quantization into native frame deadlines, audio waits and condition
variable timeouts. The kernel config and normal build script now enable
high-resolution timers. The isolated kernel build passed; it has **not** been
installed. Its effect on this device remains to be measured.

The 1.416 GHz maximum is the highest available CPU OPP on this unit, not a cap
set by Slot. `cpuinfo_max_freq` and `scaling_max_freq` both report 1416000;
`scaling_available_frequencies` ends there. The H616/H700 cpufreq nvmem driver
filters the OPP table by silicon speed bin. The 1.512 GHz entry's supported mask
is `0x2a`, while 1.416 GHz uses `0x2d`. Do not override the hardware filtering.
Forcing `performance` during Tetris DX reduced core cost but left throughput
below native rate and continued starvation. Leave light games on schedutil;
Slot already selects performance for running mGBA/GBA. Raising the CPU or GPU
floor is not a demonstrated fix for these timing problems.

## Method and limitations

The device stayed plugged into USB. PMIC USB properties expose voltage and an
input-current **limit**, not measured input current. Battery charging current
is not system draw. Residency and CPU time below are proxies, not watts or an
energy-efficiency result. Temperatures are the hottest of four thermal zones
at the end of each short sample; they are not thermal steady state.

Each test uses copied ROM input and fresh generated configuration under
`/tmp/slot-perf`, with separate temporary saves/states. No owner Config, saves
or states are copied or edited. All ROMs begin at their title/attract screens;
these are not gameplay stress tests. Both binaries use the same cores, assets,
volume (40), brightness (5), default rewind setting, and named shader. The
before binary is the installed Slot baseos2 build; after is the optimized device
build with asynchronous KMS flips. Five seconds of startup precede a 20-second
procfs sample. The final emitted 600-presentation trace supplies presentation
rate; game fps is Slot's cumulative committed-frame rate since core startup.
Trace counters include startup and are not all limited to the procfs window.

One before/after pair per configuration, in that order. These runs locate
problems; they do not meet the five alternating repeats needed for a robust
speedup or power claim. Initial shader cache state and title-screen animation
can affect results. Audio starvation counts are missing interleaved i16 samples
in Slot's software ring, not ALSA xruns. Startup overruns and silence-triggered
ALSA release/reopen appear in some cases; neither should be reported as a
steady gameplay failure without a controlled sound-producing run.

Background PID allocation includes the observer and kernel worker creation.
The first 10-second sample rose from PID 688 to 719 (~3.1/s), well below the
previous reported 12/s. The paired runs below provide additional observations.
Thread CPU percentages use `/proc/PID/task/TID/stat` deltas and USER_HZ=100;
100% is one fully busy core. GPU percentages use devfreq `trans_stat` deltas,
and CPU percentages use cpufreq `time_in_state` deltas.

## BaseOS 2 observations

Each row is one run. B = original blocking KMS, A = asynchronous KMS, both
on the same **unchanged low-resolution-timer kernel**.

| Platform/core/effect | Build | Game fps | Presents/s | CPU 480 % | CPU 1416 % | GPU 648 % | Emu CPU % | Main CPU % | Max °C | PID/s | Dropped/starved samples |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| GBC/mgba/off | B | 56.985 | 53.88 | 42.4 | 42.6 | 24.3 | 28.5 | 16.1 | 48.9 | 3.4 | 57894/57644 |
| GBC/mgba/off | A | 58.037 | 57.73 | 43.9 | 42.0 | 29.0 | 29.7 | 14.5 | 49.2 | 1.9 | 0/49262 |
| GBC/mgba/simpletex | B | 57.282 | 54.15 | 46.7 | 42.1 | 24.4 | 29.3 | 13.9 | 49.4 | 1.9 | 0/75276 |
| GBC/mgba/simpletex | A | 57.858 | 57.37 | 44.3 | 42.7 | 29.3 | 29.5 | 14.5 | 49.4 | 1.9 | 0/55534 |
| GBC/mgba/authentic | B | 33.785 | 29.18 | 56.7 | 40.8 | 79.4 | 17.9 | 9.2 | 49.7 | 1.9 | 0/738044 |
| GBC/mgba/authentic | A | 58.638 | 57.85 | 40.3 | 39.7 | 100.0 | 31.3 | 16.9 | 51.1 | 1.8 | 0/31148 |
| GB/mgba/simpletex | B | 56.116 | 52.73 | 40.4 | 40.3 | 24.9 | 32.1 | 14.7 | 49.1 | 3.2 | 122644/77542 |
| GB/mgba/simpletex | A | 58.090 | 58.30 | 34.7 | 40.7 | 28.3 | 33.4 | 13.7 | 49.6 | 1.9 | 63662/42276 |
| GBA/mgba/off | B | 56.790 | 54.58 | 0.0 | 100.0 | 38.3 | 17.8 | 8.9 | 50.6 | 1.9 | 0/102934 |
| GBA/mgba/off | A | 58.261 | 57.74 | 0.0 | 100.0 | 45.0 | 17.9 | 9.3 | 51.5 | 2.0 | 0/50918 |
| GBA/mgba/grid | B | 57.092 | 55.32 | 0.0 | 100.0 | 34.3 | 17.7 | 8.6 | 50.6 | 1.9 | 0/90800 |
| GBA/mgba/grid | A | 58.126 | 58.29 | 0.0 | 100.0 | 36.3 | 17.6 | 9.3 | 50.8 | 2.2 | 0/53610 |
| GBA/gpsp/off | B | 55.905 | 55.77 | 53.0 | 44.0 | 42.1 | 24.0 | 12.9 | 49.5 | 1.9 | 0/131056 |
| GBA/gpsp/off | A | 57.932 | 57.57 | 49.0 | 45.0 | 51.3 | 24.0 | 13.6 | 49.3 | 1.9 | 0/55888 |
| GBA/gpsp/authentic | B | 31.175 | 28.81 | 58.5 | 39.9 | 97.0 | 13.0 | 8.3 | 49.8 | 1.9 | 0/882408 |
| GBA/gpsp/authentic | A | 48.048 | 46.27 | 49.8 | 44.8 | 100.0 | 21.0 | 12.5 | 51.6 | 1.9 | 51100/331992 |

## Stock comparison and uncompleted validation

The stock 1.5.3 kernel is `4.9.170`. It reports **1 ns** monotonic timer
resolution, versus BaseOS 2's **4 ms**. A stock `timer-probe` run measured 1 ms sleeps at min/mean/max
1,062,906 / 1,078,008 / 1,278,663 ns (100 samples).
Stock exposes a 1.512 GHz CPU OPP;
BaseOS 2's upstream nvmem filtering does not expose that OPP on this unit.
This difference is not an instruction to overclock or override the mainline
OPP voltage/bin checks.

The uncontrolled stock daily session resumed Tetris DX with mGBA. A 10-second
sample allocated 124 PIDs (~12.4/s) and accumulated 815 process CPU jiffies
(~81.5% of one core across all Slot threads). GPU was 420 MHz at one sample;
schedutil was selected; the hottest SoC zone was 53.5 °C. CPU residency stats
were unavailable. These are not matched power comparisons.

A controlled stock matrix was not run. Automatic approval review rejected a
proposed RAM-only bind mount of a test launcher over stock's
`/usr/sbin/frontend-session`, citing an unapproved service override and
frontend-disruption risk. That action was not executed. No replacement
service override was attempted. Uncontrolled daily-session stock readings
cannot establish equal game/core/shader conditions and are not a parity result.

## Validation and next step

- Optimized Slot device builds passed. Five native frame-pacer unit tests
  passed, including slow-panel drift and fallback recovery. The attempted DRC
  unit-test filter matched no tests; it is not a DRC validation result.
- Kernel Image built successfully in isolated `/k/slot-performance-build`,
  with high-resolution timers enabled by olddefconfig. No shared kernel-tree
  sources or default build output were changed. The initial host-helper link
  failure was resolved by unsetting the frontend pkg-config sysroot variables,
  as the normal kernel build script does.
- Candidate kernel: `work/v2/kernel/slot-performance.Image`. This is a newly
  built artifact, not a rollback copy. It has not been booted or installed.
- Need approval to test the new kernel image and an approved way to isolate
  stock's frontend for matched runs. Then repeat the paired matrix at least
  five times, include actual gameplay and sustained audible music, inspect
  physical frame intervals and ALSA continuity, and sample thermal steady
  state before claiming stock parity or battery/thermal improvement.
- The authentic effect remains expensive: gpSP/GBA reaches only 46.27
  presents/s with asynchronous KMS and 100% residency at GPU 648 MHz. Its
  shader/GPU cost needs a separate profile after fixing timer resolution.
  Current evidence does not justify changing shader appearance or voltage.

`SLOT_START_SHELF=1` starts Slot on its shelf without changing the owner's
remembered cartridge setting. This allows the final device handoff on the shelf
without editing Config or retiring a resume state. The temporary frontend
wrapper is removed after that startup.

## Handoff

Installed Slot commit `ad047f7` by pushing `System/.slot.new` and renaming over
`System/slot`. Returned through the documented stock latch + `lab.py boot`
path to `7.3.1-baseos2+` in play mode. Camera inspection confirms the shelf,
with no game running. Removed `/data/baseos2/frontend-wrap`; reboot cleared
the experiment's `/tmp` files. No staging files, rollback binaries, measurement
manifests or screenshots were left on the card. No pushes were made.
