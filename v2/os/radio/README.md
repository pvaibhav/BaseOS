# Radio build inputs

These files were downloaded inside containers. Nothing was installed on the
Mac. `build.py root` verifies the hashes and installs them without a download.
`fetch.py` fetches the pinned firmware again in `v2-kbuild`; compare the manifest
when updating it. `verify.py` checks the saved inputs. `check.py` extracts the
built squashfs to `/k/radio-check`, runs checks and fixtures there, and removes
that extraction on success. It never accesses a handheld.

Firmware provenance and SHA-256 hashes: `manifest.json`. Linux-firmware commit
`377495d9a54c5c3b25b59fbc3068e29aab2c78d0`; wireless-regdb `2026.09.03`.
The Bluetooth config is the `rtl8761b_config.bin` content that upstream WHENCE
assigns to `rtl8821cs_config.bin`. It is not a stock board config substitution.
Realtek's licence covers both Wi-Fi and Bluetooth; the regdb licence and the
upstream WHENCE file are retained. The signed regdb pair is kept together.

The arm64 packages came from Debian bookworm's main archive,
`http://deb.debian.org/debian`, through `apt-get download` in
`debian:bookworm-slim`. Exact versions and SHA-256 hashes are in the filenames
and `packages/SHA256SUMS`:

- wpasupplicant 2:2.10-12+deb12u3: supplicant, CLI, passphrase tool.
- iw 5.19-1.
- libpcsclite1 1.9.9-2: supplicant dependency.
- bluez 5.66-1+deb12u2: only bluetoothctl is installed, plus package copyright.

Package init scripts, systemd units and daemon configurations are not installed.
The existing BlueZ daemon (5.66), BlueALSA (4.2.0-16-gdd85a7b), ALSA plugins,
libnl, OpenSSL, D-Bus and readline are reused. The inherited bluetoothctl fails
without a bus and hangs on --help with an isolated bus, so it is replaced with
the standard client. All new package copyright files are installed in
`/usr/share/doc`. Firmware licences also live in `/usr/share/licenses/baseos-radio`.

Re-fetch packages, if needed, inside a Debian container with the repository's
`v2/os/radio` mounted at `/out`:

```
apt-get update
cd /out/packages   # /out = work/v2/radio
apt-get download wpasupplicant=2:2.10-12+deb12u3 iw=5.19-1 libpcsclite1=1.9.9-2 bluez=5.66-1+deb12u2
sha256sum -c SHA256SUMS
```

The saved packages avoid depending on their continued availability at that URL.

The downloaded files are kept outside git, in `work/v2/radio/` (firmware/ and
packages/); git tracks only `manifest.json` and `packages/SHA256SUMS`, which
`verify.py` checks them against before every root build.
