#!/usr/bin/env python3
"""Build real payloads when available, then run per-model offline tests."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
R=Path(__file__).resolve().parents[2];W=R/'work/v2/firmware'
targets=sys.argv[1:] or [t for t in json.loads((R/'v2/firmware/targets.json').read_text()) if t not in ('rg28xx','rgsp')]
def verify(t):
    if not (W/('v2-'+t)/'manifest.json').exists():
        print(t,'firmware not built',flush=True);return 1
    dtb=R/f'work/v2/kernel/{t}.dtb'
    commands=[]
    if dtb.exists():commands.append([sys.executable,R/'v2/os/build.py','payload','--firmware','v2-'+t,'--dtb',dtb])
    commands.append([sys.executable,R/'v2/firmware/tests/fleet.py',t])
    with (W/f'verify-{t}.log').open('w') as log:
        for command in commands:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
            if result.returncode:print(t,'FAIL',flush=True);return result.returncode
    print(t,'pass (emulated)',flush=True);return 0
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    sys.exit(any(list(pool.map(verify,targets))))
