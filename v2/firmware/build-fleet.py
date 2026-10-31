#!/usr/bin/env python3
"""Build untested fleet candidates; never accesses devices or existing baseline outputs."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
R=Path(__file__).resolve().parents[2]
W=R/'work/v2/firmware'
targets=[t for t in json.loads((R/'v2/firmware/targets.json').read_text()) if t not in ('rg28xx','rgsp')]
def build(t):
    with (W/f'build-{t}.log').open('w') as log:
        p=subprocess.run([sys.executable,R/'v2/firmware/build.py','v2-'+t,'--target',t],stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    print(t, p.returncode,flush=True)
    return p.returncode
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    sys.exit(any(list(pool.map(build,targets))))
