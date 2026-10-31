# Slot performance on the owner's RG SP

2026-11-01. Device `ac001089c89588720d2`. **Investigation remains open:** the
timer-enabled baseline still starves audio; the follow-up Slot fix restores native
game speed with flat starvation counters in all eight short samples.
Stock parity, battery parity and thermal parity have not been established.

The original report is retained below. The timer-enabled follow-up at the end
supersedes its pending-install and approval statements.

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

## Timer-enabled follow-up (2026-11-01)

Installed kernel `f5dae3a`, `7.3.1-baseos2+ #33`, reports 1 ns timer
resolution. This follow-up uses the same installed Slot `ad047f7` program
(SHA256 `813b6795e699cfa59b8fa0e8102e0b3c201624f1bf0b48c51a39398247431643`),
ROM/core/effect matrix, RAM-isolated configuration and 5 + 20 second sampling
method. `SLOT_TRACE=1 SLOT_TRACE_LATENCY=1` enables the two counters. An initial
repeat accidentally overlapped observers; those results were discarded and
both observers stopped before the clean matrix.

The CPU fuse's low 16 bits are `0x7c00`. The mainline H616 translator maps
that fuse to bin 1 for revisions A/B and bin 2 for C or later. This unit's
available frequency list matches bin 2: 1.416 GHz's mask `0x2d` includes it,
1.512 GHz's `0x2a` excludes it. Boost is off and its available-frequency list
is empty. `vdd-cpu` permits 900–1160 mV and was observed at 1100 mV. The
1.416 GHz cap is therefore OPP hardware filtering, not the DCDC1 maximum.
No frequency, voltage or supported-hardware mask has been changed.

| Platform/core/effect | After timers game fps | After timers presents/s | CPU 480 % | CPU 1416 % | GPU 648 % | Emu CPU % | Main CPU % | Max °C | PID/s | Dropped/starved samples |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| GBC/mgba/off | 58.550 | 58.62 | 76.5 | 22.5 | 31.8 | 35.4 | 16.7 | 48.8 | 2.1 | 59618/30294 |
| GBC/mgba/simpletex | 58.211 | 57.45 | 73.1 | 25.1 | 31.0 | 33.5 | 18.7 | 48.9 | 3.3 | 59638/26124 |
| GBC/mgba/authentic | 58.569 | 57.56 | 82.6 | 15.3 | 100.0 | 35.4 | 19.0 | 50.8 | 1.9 | 0/35346 |
| GB/mgba/simpletex | 58.295 | 58.31 | 71.6 | 20.6 | 31.1 | 41.4 | 16.4 | 49.4 | 2.5 | 62722/41090 |
| GBA/mgba/off | 57.814 | 57.55 | 0.0 | 100.0 | 51.9 | 17.0 | 9.4 | 51.0 | 1.9 | 55720/57610 |
| GBA/mgba/grid | 57.688 | 57.72 | 0.0 | 100.0 | 34.6 | 16.9 | 9.4 | 51.2 | 2.3 | 54236/57456 |
| GBA/gpsp/off | 58.390 | 58.12 | 78.7 | 20.2 | 52.0 | 25.2 | 15.1 | 49.6 | 1.9 | 58652/37862 |
| GBA/gpsp/authentic | 47.563 | 46.73 | 88.3 | 10.6 | 100.0 | 21.4 | 13.8 | 51.7 | 1.9 | 0/369592 |

One clean run per case; these are diagnostic observations, not a repeated power comparison. Timers have not eliminated starvation or the authentic GPU limit.

### Slot changes under validation

`GamePass` specializes the selected effect, LCD model, gamma and grain as
GLSL constants. The colour response, grid, noise formula, glass, source
texture formats, ghosting and render resolution remain the same. This lets
Mesa remove unused effect branches and the GBA's zero-strength noise path.
The universal shader remains a fallback on shader compile failure.
Mesa already uses a release Panfrost-only build with shader caching; the
GPU governor is `simple_ondemand`, with 648 MHz available at the retained
940 mV rail. No Mesa, devfreq or voltage changes have been made.

A shader-only diagnostic trial (one run each) measured gpSP authentic at
57.60 presents/s, versus 46.73 after timers alone, both with 100% GPU 648 MHz
residency. GBC authentic stayed near 58 presents/s and 100% residency.
This identifies unused shader work as a substantial GBA bottleneck, but does
not establish power parity or thermal steady state.

Frame-stage tracing found repeated fallback frames even with effects off.
Two coupled bugs lost native game time: the pacer rebased its timeline on
each fallback, and the worker could acknowledge a later tick using an
already-completed fallback frame. The pacer now advances by the number of
completed fallback frames, and a tick arriving after completion runs a new
frame. A tick arriving during an in-flight fallback is still acknowledged
exactly once. A new core resetting its counter resets the timeline.

The phase regression failed before the fix and passes afterward. Validation:
82 Slot unit tests passed (one ignored), all 46 emulator behavior tests,
22 audio tests and all 39 rendering pipeline tests passed; the device-feature
Clippy check passed with warnings denied. ARM64 optimized device builds pass.

### Complete pacing fix, before the floating-point ramp

| Platform/core/effect | Game fps | Presents/s | CPU 480 % | CPU 1416 % | GPU 648 % | Emu CPU % | Main CPU % | Max °C | PID/s | Dropped/starved samples | Steady starvation growth |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| GBC/mgba/off | 59.797 | 58.97 | 76.3 | 22.8 | 0.0 | 36.2 | 16.3 | 48.1 | 2.0 | 0/5120 | 0 |
| GBC/mgba/simpletex | 59.813 | 58.97 | 75.0 | 23.6 | 32.2 | 36.1 | 16.5 | 49.4 | 2.0 | 0/5120 | 0 |
| GBC/mgba/authentic | 59.797 | 57.73 | 81.3 | 17.0 | 99.7 | 32.8 | 18.9 | 50.4 | 1.9 | 0/5120 | 0 |
| GB/mgba/simpletex | 59.697 | 58.88 | 74.4 | 23.5 | 30.6 | 43.2 | 16.5 | 49.1 | 1.9 | 63038/5120 | 0 |
| GBA/mgba/off | 59.683 | 58.88 | 0.0 | 100.0 | 0.0 | 17.6 | 9.1 | 50.5 | 1.9 | 62128/9216 | 0 |
| GBA/mgba/grid | 59.660 | 58.57 | 0.0 | 100.0 | 26.7 | 17.4 | 8.9 | 50.1 | 1.9 | 61652/8192 | 0 |
| GBA/gpsp/off | 59.715 | 59.06 | 75.5 | 23.5 | 0.0 | 25.7 | 14.9 | 48.2 | 2.0 | 62230/5120 | 0 |
| GBA/gpsp/authentic | 59.713 | 58.21 | 81.8 | 16.5 | 99.7 | 24.3 | 17.3 | 50.4 | 1.9 | 62948/5120 | 0 |

All eight logged intervals have zero starvation growth after the first 300-frame report. Cumulative startup drops/starvation are retained in the table; they have not been hidden or reset. Game rates approach the core nominal 59.7275 fps. Presents count submissions, not physical camera frames.

The 32-code floating-point gamma-ramp experiment was tested and discarded:
GBC authentic measured 57.08 presents/s, gpSP authentic 58.31, with GPU
648 MHz residency 97.9% and 95.7%. This did not establish a useful
presentation improvement over the simpler specialization. Its source and
floating-point texture changes are not part of the installed result.

The render path retains a 240×160 BGRA8 game texture and two 240×160 RGBA8
LCD-response textures, a 720×480 RGBA8 scene FBO and the final 720×480
scanout. Its main stages are the LCD response, game/effect composition and
final scene blit, with UI/bezel draws as applicable. No pass or resolution
was removed. The running mode uses 26.182 MHz / 820 / 536, approximately
59.57 Hz: native 59.7275 Hz emulation necessarily occasionally advances
without presenting every emulated frame. The remaining 648 MHz residency
means authentic has limited render headroom, even after the native/audio fix.

Compiler profiling was separate from timed runs, with shader caching disabled
and `BIFROST_MESA_DEBUG=shaderdb,statsfull`, on verified gpSP/GBA authentic
launches. The universal fragment program uses 21 work registers, 60 uniform
registers and 8352 bytes of code; the selected specialized fragment uses
12 work registers, 38 uniform registers and 2096 bytes. Compiler static
instruction estimates are 24.250 arithmetic / 8.000 texture cycles for the
universal program and 6.333 / 2.000 for the specialization. Both have zero
spills/fills and zero stack. These are compiler estimates for the programs,
not a measured fourfold GPU speedup. The larger universal program is still
compiled during initial compositor construction as the portable fallback.

Slot changes are committed as `512d3ef` on `baseos2`; no push was made.

### BIOS-backed gameplay check

The inherited RAM matrix contains no BIOS directory. Its title-screen/HLE
results remain the matched baseline above, not gameplay stress results. For
a separate check, the owner's BIOS inputs were copied into the RAM root,
without changing the originals. Actual gpSP/GBA authentic Tetris Marathon
play was confirmed by camera (running board and game timer). A 20.08-second
procfs sample ran during gameplay; ALSA was RUNNING at both endpoints.
Cumulative game fps was approximately 59.69. The dropped and starved counters
stayed at 191768 and 7168 throughout the gameplay sample; the accumulated
drops include earlier setup/fast-forward transitions and were not reset.
The startup/title setup, sound-producing menu, and gameplay were kept
separate when interpreting continuity. This is one short gameplay sample;
a 30 fps camera cannot resolve each physical 59.57 Hz scanout interval.

Installed committed Slot SHA256:
`79a2a947bbaf7978435841d3c7545abda8e55af68eefa4841c2f14886b6b0b30`.

### Stock 1.5.3 inspection with the same installed Slot binary

Switched using `lab.py stock`, without changing the play payload or boot
firmware. `/usr/sbin/frontend-session` selects the card launcher and directly
executes `/bin/sh "$LAUNCH"`; it has no environment-wrapper hook. No stock
file, card frontend launcher, service override or bind mount was changed.
The permitted fallback was used: outside observations of the daily session,
not a claimed matched eight-case stock matrix.

Stock's exact available CPU list on this unit is:
`480000 720000 936000 1008000 1104000 1200000 1320000 1416000 1512000`.
`schedutil` was selected and 1512000 was directly observed. Its live
`axp2202-dcdc1` rail was observed at 1160 mV. The stock DT's generic
`opp@1512000000` lists 1120000 µV and supported-hardware mask 1; actual
selected-bin/runtime rail voltage is the relevant observation, rather than
assuming that generic entry is the active voltage on this unit.
Mainline still limits this bin to 1.416 GHz at 1.100 V, within its
900–1160 mV rail constraints. Since CPU-bound loss is no longer present in
the measured cases, no OPP override or extra game CPU floor was added.

The unchanged daily session is Tetris DX/GBC/mGBA/authentic, brightness 0
and volume 5, rather than the RAM matrix's brightness 5 / volume 40. Stock
reports a 720×480 canvas (fb0 virtual buffer 720×960), so this comparison
does not reveal a lower stock rendering resolution. Its GPU clock was
420 MHz at direct observations, and ALSA was RUNNING. CPU residency
`time_in_state` is unavailable. These settings and sessions are not matched
and cannot establish watts, energy parity, or a stock-relative speedup.

A 20.21-second external stock sample measured main-thread CPU 24.2% and emulator CPU 31.8%, with maximum endpoint temperature 53.8 °C. ALSA was RUNNING at both endpoints. These are one-core CPU-time percentages, not power.

### Final handoff and remaining work

Returned via native-health stock hold/release and `lab.py boot` to the same
BaseOS 2 **play payload**, with its existing timer-enabled kernel. No kernel,
DTB, payload or boot-firmware change was needed for the fixes in this pass.
A temporary `SLOT_START_SHELF=1` environment override selected the shelf
without editing the owner's remembered cartridge. Camera inspection confirms
the shelf; the frontend has no `slot-emu` thread. The override was then removed.
The restart cleared all experiment RAM files, including copied ROM/BIOS
inputs, generated saves/states/configuration, test binaries and evinject.
`System/.slot.new` is absent. No backup, release archive, verification manifest,
screenshot or installer script was left on the card. Only `System/slot` was
replaced. No pushes were made.

**Still open:** battery/energy parity and sustained thermal parity against
stock are not established. Stock's unmodified launcher cannot run the
isolated matrix, and the daily-session readings have different brightness,
volume and startup history. USB input limits and battery charging current
are not system watts. Authentic still uses high mainline GPU frequency;
648 MHz residency is frequency residency, not a measured 100% GPU utilization.
Five alternating repeats, longer sound-producing gameplay across the other
cores/platforms, thermal steady state and higher-speed physical frame timing
remain necessary before a broad efficiency or frame-delivery parity claim.
The demonstrated result is native emulation and no steady software audio
starvation in the eight short matrix runs and the BIOS-backed Marathon sample.
