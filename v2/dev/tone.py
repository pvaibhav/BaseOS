#!/usr/bin/env python3
"""Play a 1 kHz tone on the device and measure it with the iPhone mini mic.

Usage: tone.py [label]   (the device must have /tmp/tone.wav; lab.py pushes it)
Prints the 1 kHz level per 0.5 s window; tone windows should stand far above
the silent ones before and after.
"""
import array, math, subprocess, sys, time, wave
from pathlib import Path

LAB = Path(__file__).with_name('lab.py')
OUT = Path('/private/tmp') / f'v2-tone-{sys.argv[1] if len(sys.argv) > 1 else "test"}.wav'


def level(x, f, r):
    k = 2 * math.cos(2 * math.pi * f / r); s1 = s2 = 0.0
    for v in x:
        s1, s2 = v + k * s1 - s2, s1
    return math.sqrt(max(s1 * s1 + s2 * s2 - k * s1 * s2, 0)) / len(x)


rec = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'avfoundation', '-i', ':0',
                        '-t', '6', '-ac', '1', '-ar', '48000', '-y', str(OUT)])
time.sleep(1.5)
subprocess.run([sys.executable, str(LAB), 'sh', 'aplay -q /tmp/tone.wav'], check=True)
rec.wait()
w = wave.open(str(OUT)); r = w.getframerate()
a = array.array('h'); a.frombytes(w.readframes(w.getnframes()))
levels = [level(a[i:i + r // 2], 1000, r) for i in range(0, len(a) - r // 2, r // 2)]
print(' '.join(f'{v:.0f}' for v in levels))
quiet = sorted(levels)[:3]
print(f'peak {max(levels):.0f} vs background {sum(quiet) / 3:.0f}: '
      f'{20 * math.log10(max(levels) / max(sum(quiet) / 3, 0.01)):.0f} dB above background')
