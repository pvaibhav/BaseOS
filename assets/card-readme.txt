Base OS — add a frontend to this card
======================================

This card runs Base OS: a minimal Linux that boots your Allwinner H700
handheld and hands off to a frontend. Base OS itself ships no frontend, so
this data partition has setup files and is ready for you to add a frontend.

To install NextUI:

  1. Copy NextUI's release files onto the root of this card:
        MinUI.zip
        (and any nextui.*.pakz files from the same release)
  2. Optionally add your Roms / Bios / Saves later — the installer creates
     those folders on first run.
  3. Put the card back in the handheld (TF1 slot) and power on.

Base OS detects MinUI.zip, runs NextUI's installer, and launches it. On every
boot after that it goes straight to NextUI in a few seconds.

To install Slot (a GBA frontend designed for the Anbernic RG SP):

  1. Extract Slot's H700 release on your computer.
  2. Copy the contents of its slot-<version> folder to the root of this card.
  3. Add games to Games/ and an optional gba_bios.bin to BIOS/, then reboot.

To update Slot, replace its System folder with the one from a new release.

To install spruceOS:

  1. Copy the extracted H700 release contents to the root of this card.
  2. Put the card back in the handheld and reboot.

To install another compatible frontend:

  Follow its installation instructions and copy its release files to the
  root of this card, then reboot.
  Base OS launches System/frontend (binary), or System/launch_frontend.sh
  if the binary is absent.

Base OS detects and starts your installed frontend automatically. You can
also use a FAT32/exFAT card in TF2; when inserted, that card is used instead
of this card's data partition.

Frontend setup and developer details:
https://github.com/pvaibhav/BaseOS#installation

Updating Base OS
----------------

You never need to reflash to move to a new Base OS version. Copy the release's
.bosupd file onto this card and power the handheld on. Base OS installs it to
its spare system slot, checks it, switches over and restarts — about a minute.

Your Roms, Bios, Saves and settings are not touched, and the previous version
stays on the card. If initialization repeatedly fails before the frontend
session starts, Base OS can roll back automatically. Failures before the
update checker can run require recovery or reflashing. The applied .bosupd
file is deleted automatically after the update is verified and committed.

This card's whole capacity is available now — Base OS expanded it to fill the
card on first boot.

USB cable access
----------------

adb is available automatically over a data-capable USB-C cable.

To start writable USB-storage mode, hold MENU while powering on and keep it
held until the USB-storage message appears. With a second card inserted, Base
OS exports that complete card; otherwise it exports this card's data volume.
adb remains available at the same time.

Safely eject the disk on the computer, then restart without MENU to return to
normal. Never restart or unplug the cable while the computer is writing.

Base OS settings
----------------

Edit baseos.conf at the root of TF1, then restart normally:

    hostname=my-handheld
    mdns=true

The default hostname is the device model ID (for example, rg34xxsp), and
mDNS enables that name with .local on Wi-Fi. Set mdns=false to disable it.
Hostnames accept 1-63 ASCII letters, digits and hyphens, with no hyphen at
either end. TF1 supplies settings even when the frontend is on TF2.

Full settings reference, including SSH password configuration:
https://github.com/pvaibhav/BaseOS#settings

Boot diagnostics
----------------

BaseOS appends boot diagnostics to baseos-boot.log at the root of the active
frontend card: TF2 when usable, otherwise TF1. Each start gets a header, its
startup time, and a "Loader:" line that says whether the fast loader started
it and, if not, why. After an update, or when something about the start
changed, a "Diagnostics" block follows with the hardware and boot details
needed to report a problem. It holds no Wi-Fi settings, addresses or serial
numbers. The log trims its oldest starts by itself.

Optional startup sound
----------------------

Copy startup_sound.wav to the root of TF1's BASEOS partition to play it once
in the background during normal startup. Use a 48 kHz stereo, 16-bit PCM WAV.
Delete or rename the file to disable the sound. TF2 is not searched.
Playback uses full codec volume and stops after at most 30 seconds; it does
not delay frontend launch. No sound file is bundled with BaseOS.
