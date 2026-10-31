#!/usr/bin/env python3
"""Check the inputs before putting them in the root image. Run in a container."""
import hashlib, json, subprocess
from pathlib import Path
r = Path(__file__).resolve().parent
# Downloaded files live outside git, in work/v2/radio (see README.md).
a = r.parents[2] / 'work/v2/radio'
for entry in json.loads((r / 'manifest.json').read_text())['files']:
    assert hashlib.sha256((a / 'firmware' / entry['path']).read_bytes()).hexdigest() == entry['sha256'], entry['path']
for line in (r / 'packages/SHA256SUMS').read_text().splitlines():
    digest, name = line.split()
    assert hashlib.sha256((a / 'packages' / name).read_bytes()).hexdigest() == digest, name
subprocess.run(['openssl', 'cms', '-verify', '-binary', '-inform', 'DER',
                '-in', str(a / 'firmware/regulatory.db.p7s'),
                '-content', str(a / 'firmware/regulatory.db'),
                '-noverify', '-out', '/dev/null'], check=True)
print('Radio firmware and package hashes verified')
