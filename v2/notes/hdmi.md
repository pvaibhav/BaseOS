# H700 mainline HDMI: build-only test series

Prepared 2026-11-01. No handheld was contacted or installed. Video, hot-plug,
audio, LCD recovery and suspend are all untested on H700.

The first target is **RG SP** (`anbernic,rgsp`, the 720×480 model), not
RG35XX SP. `/k/rgsp-stock.dts`, extracted from its normal stock boot tree,
enables HDMI at 0x06000000 and describes the `ahubhdmi` sound card. The
read-only stock evidence is under `work/rgsp` and `work/hdmi-analysis` in
the original BaseOS checkout. RG28XX has no HDMI connector; this series
does not enable HDMI on it or on the other models.

## Sources and choice of port

The existing BaseOS patches already contain the split DE33 plane driver,
both mixer descriptions, LCD0 and the panel driver. Mixer1 was disabled;
there was no HDMI PHY variant or TV TCON/HDMI graph. HDMI, TV TCON and
audio-hub CCU clock/reset definitions already existed.

Ryan Walklin's [LCD v2 series](https://lore.kernel.org/all/20250720085047.5340-1-ryan@testtoast.com/)
is background for the LCD path. It builds on work by Jernej Skrabec and
Chris Morgan; it explicitly leaves HDMI for later work.

The September [Barzin Lotfabadi HDMI proposal](https://github.com/BarzinL/h616-hdmi-mainline)
is useful evidence, but its README records the maintainer's rejection of
the old mixer layout and H6 TCON TOP fallback. Do not apply its four-patch
recipe on top of this series. The README also records a successful H618
test of Jernej's replacement branch; that is not H700 test evidence.

This port uses Jernej Skrabec's [display refactor series](https://lore.kernel.org/linux-sunxi/20260509190015.79086-1-jernej.skrabec@siol.net/)
and [test branch](https://github.com/jernejsk/linux-1/tree/sun4i-drm-refactor),
plus the newer HDMI audio work in his
[LibreELEC patch tree, pinned at ed5140a734c32c99ba4836dd0d014b2c74c759f0](https://github.com/jernejsk/LibreELEC.tv/tree/ed5140a734c32c99ba4836dd0d014b2c74c759f0/projects/Allwinner/patches/linux).
The relevant LibreELEC patches are 0209, 0211, 0212, 0500, 0501, 0503,
0505, 0518, 0519, 0520, 0700, 0704 and 0705. Source authors and links are
recorded in the port commits. This is a downstream testing series, not a
claim that all these changes have merged upstream. No newer standalone
mailing-list HDMI version was found in this survey.

## What the series provides

- H616-specific TCON TOP support, rather than the H6 fallback.
- H616 TV TCON support, including the HDMI pad-select bit.
- H616 HDMI PHY MPLL/current/voltage tables and the H6-compatible DW-HDMI
  controller. DW-HDMI supplies EDID/DDC and interrupt-driven HPD; there is
  no invented GPIO or vendor extcon emulation.
- Mixer0 → TCON LCD0 → panel, and mixer1 → TCON TV0 → TCON TOP → HDMI.
  The inherited mixer1 → LCD0 route stays disabled. Both mixers use the
  existing shared-plane driver, which already assigns separate planes to
  each mixer. TV1 and LCD1 are outside this first port.
- H616 AHUB driver, HDMI audio machine driver and audio clock fixes.
  The internal path is DMA → AHUB APBIF → I2S output 1 → DW-HDMI codec.
  No external I2S pinmux is needed. The source driver supports PCM and
  IEC958/HBR transport; begin testing with stereo PCM only.
- A kernel config fragment at `arch/arm64/configs/baseos-hdmi.config`.

Power remains a qualification gap. Stock names the HDMI supplies
`vcc-hdmi` and `vdd-hdmi` without mapping them to PMIC outputs in the
inspected tree. The board uses the existing retained-boot 3.3 V regulator
representation for `hvcc-supply`. This does not turn on a newly discovered
rail, and does not prove either HDMI supply or connector 5 V is present.
No guessed PMIC voltage writes were added. Check the rails if HPD/DDC or
PHY lock fails, and replace this representation once wiring is established.

The LCD and HDMI clocks may share PLL ancestors. Concurrent output and
changing HDMI modes while the panel is active need device testing. Keep
the first userspace implementation to one active output. CEC, HDCP, HDR,
deep color, 4K, compressed audio and simultaneous display are not qualified.
The imported audio drivers do not include complete upstream DT schemas;
schema validation was not performed (`dtschema` is absent in the container).

## Build and exported patches

The isolated container worktree is `/k/linux-hdmi`, branch
`baseos-h700-hdmi`. Base: `967ad2339` (BaseOS kernel patches 0001–0040).
Head: `5300c191bc80d1a7fc2f8e3b927407933a5ccdb3`.
The nine patches are in `v2/kernel/hdmi-patches/`, in filename order.
They apply after the existing kernel patch series; they do not replace it.
All new commits use Prashant Vaibhav <prashant@vaibhav.de> as author.

`/k/build-hdmi/.config` started as a copy of `/k/build/.config`.
Inside `v2-kbuild`, reproduce with:

```sh
cd /k/linux-hdmi
scripts/kconfig/merge_config.sh -m -O /k/build-hdmi \
  /k/build-hdmi/.config arch/arm64/configs/baseos-hdmi.config
make O=/k/build-hdmi ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
env -u PKG_CONFIG_PATH -u PKG_CONFIG_SYSROOT_DIR \
  make -j6 O=/k/build-hdmi ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  Image modules dtbs
```

Clearing those environment variables prevents the container's target
OpenSSL sysroot from contaminating the host certificate-tool build.
The final build passed with no compiler warnings or errors in its log.
Image, 33 modules and all DTBs built. Build log: `/tmp/hdmi-build.log`
inside the container. Nothing was installed on the Mac or a handheld.

Artifacts remain in `/k/build-hdmi`:

- `arch/arm64/boot/Image`, SHA-256
  `d3ac852af9731a4d80cc570eeaa62d761d9ba9e903ad849c4128e6384222db6e`.
- `arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rgsp-baseos.dtb`,
  SHA-256 `b0fef32056c5794921b9275f318c2a96fad64bc9fe85ad0703ec36f0abf774f0`.

The built RG SP DTB has all eight expected display/audio blocks enabled,
18 reciprocal graph endpoint references, AHUB DAI 1 selected, the unwanted
cross-route disabled, and all 47 explicitly pinned loader phandles retained.
Automatically assigned phandles can change; the check covers the sealed
loader values. Replaying all exported patches into a temporary Git index
at the base produced the exact head tree. `git diff --check` passed.

## Proposed `baseos display output lcd|hdmi|auto`

This series adds kernel support; it does not implement that command.
The display owner should enumerate DRM resources and select connectors by
type and availability. Expect the panel as DPI and HDMI as HDMI-A; discover
their IDs at runtime rather than pinning numeric IDs or `card0`.

`lcd` should select the panel's native mode and its allowed CRTC/primary
plane. `hdmi` should require a connected HDMI connector and an EDID mode,
then select its allowed CRTC/primary plane. An unplugged explicit HDMI
request should return an error and preserve the current working display.
`auto` should prefer connected HDMI with a usable mode, otherwise LCD.
Subscribe to DRM hotplug uevents and rescan after a short debounce; do not
poll the old vendor `/sys/class/extcon/hdmi` interface.

The process holding DRM master must perform the switch. Pause rendering,
allocate a correctly sized buffer, use an atomic TEST_ONLY modeset, then
commit the new connector/CRTC/plane state while disabling the old output
in the same transaction. Resume rendering after the modeset completes.
Keep or restore the working output on failure. Turn the panel backlight
off after a successful HDMI switch, and restore its saved brightness after
LCD recovery. Recreate SDL/GBM surfaces if their dimensions change.

Audio selection is separate: discover the HDMI ALSA card and its ELD,
drain/reopen playback on switching, then mute the speaker only after HDMI
audio opens successfully. Preserve the user's volume. The default imported
card name is `sunxi-hdmi`, not the stock `ahubhdmi`; never select by numeric
card index. On unplug, restore the existing speaker/headphone policy.
Do not use the BSP `dispdbg` switch controls on this DRM kernel.

## Ordered device test plan

1. Use the RG SP Image and its matching DTB through the existing test boot
   path. Preserve games, saves, settings and credentials. Keep logs and
   temporary files in device `/tmp`; leave no installer files on the SD.
2. Cold boot without HDMI. Confirm panel adoption, input, brightness and
   speaker/headphone audio. Capture DRM component bindings, deferred probes,
   clocks and regulator state. A missing HDMI component must not silently
   prevent the entire DRM device from binding.
3. Enumerate DRM connectors, CRTCs and planes with `modetest -M sun4i-drm -c -p`.
   Expect panel and HDMI connectors, two CRTCs, and separate primary planes.
   Save the actual IDs. Check no-cable HDMI status is disconnected.
4. Plug in a known-good mini-HDMI cable and a 720p/1080p sink. Check HPD
   uevents, connected status and a valid EDID. If absent, check supplies,
   connector 5 V, DDC and IRQ 63 before changing PHY tables.
5. Stop the normal DRM owner, disable LCD scanout, and show color bars at
   720p60 RGB 8-bit using the advertised HDMI CRTC/plane. Then try 1080p60.
   Inspect PHY lock, pixel/TMDS clocks, colors, cropping and frame stability.
6. Exercise LCD → HDMI → LCD through the proposed display owner, then
   repeated plug/unplug and boot with cable already connected. Confirm panel
   brightness and native mode recover, including both panel revisions.
   Record failures before attempting concurrent output.
7. Confirm the HDMI ALSA card and ELD. Play stereo PCM at 48 kHz, then
   44.1 kHz. Check pitch, speed, channel order, underruns and speaker mute.
   Test unplug while playing and return to headphones/speaker. Only then
   consider multichannel or compressed transport with a suitable receiver.
8. Test lid close/open, suspend/resume, hotplug during suspend, different
   sinks and adapters, and a game launch/exit. Check LCD and HDMI clocks
   during each transition. Qualify each additional HDMI model separately
   before enabling its DT nodes.
