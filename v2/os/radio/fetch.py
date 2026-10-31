#!/usr/bin/env python3
"""Run inside v2-kbuild to fetch the pinned upstream radio firmware."""
import hashlib, io, json, tarfile, urllib.request
from pathlib import Path
out = Path('/repo/work/v2/radio')  # downloads stay outside git
lists = Path('/repo/v2/os/radio')
commit = '377495d9a54c5c3b25b59fbc3068e29aab2c78d0'
base = f'https://gitlab.com/kernel-firmware/linux-firmware/-/raw/{commit}/'
entries = []
def save(path, data, url):
    p = out / 'firmware' / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    entries.append(dict(path=path, sha256=hashlib.sha256(data).hexdigest(), source=url))
for path, source in [('rtw88/rtw8821c_fw.bin', 'rtw88/rtw8821c_fw.bin'),
                     ('rtl_bt/rtl8821cs_fw.bin', 'rtl_bt/rtl8821cs_fw.bin'),
                     # WHENCE declares this alias; it is made by copy-firmware.sh.
                     ('rtl_bt/rtl8821cs_config.bin', 'rtl_bt/rtl8761b_config.bin'),
                     ('LICENCE.rtlwifi_firmware.txt', 'LICENSES/LICENCE.rtlwifi_firmware.txt'),
                     ('WHENCE', 'WHENCE')]:
    url = base + source
    save(path, urllib.request.urlopen(url).read(), url)
url = 'https://www.kernel.org/pub/software/network/wireless-regdb/wireless-regdb-2026.09.03.tar.xz'
data = urllib.request.urlopen(url).read()
with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
    for path in ['regulatory.db', 'regulatory.db.p7s', 'LICENSE']:
        save('wireless-regdb-LICENSE' if path == 'LICENSE' else path,
             archive.extractfile('wireless-regdb-2026.09.03/' + path).read(), url)
(lists / 'manifest.json').write_text(json.dumps(dict(linux_firmware_commit=commit, wireless_regdb_version='2026.09.03', wireless_regdb_archive_sha256=hashlib.sha256(data).hexdigest(), files=entries), indent=2) + '\n')
