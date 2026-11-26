# 04. Boot splash

The bootloader shows a static BaseOS logo until the frontend draws its first
frame. Ordinary boots do not run a splash renderer or write to the framebuffer.
Custom `bootlogo.bmp` artwork needs no runtime detection or setting.

## 1. Framebuffer renderer

`src/fbsplash.c` builds a static renderer with this interface:

```text
fbsplash <progress 0-100>               full-screen static logo
fbsplash <progress 0-100|-1> <message>  compact status pill overlay
```

A message selects the pill overlay, and `-1` hides its progress track. Without
a message, the renderer draws the full-screen logo, which bootlogo generation
and charger-mode startup feedback use. Runtime scripts call it through
`baseos-splash` only for exceptional work or a state that needs user action.
Option-shaped arguments make it exit with status 2 without drawing.

The pill keeps the pixels outside its status area. Each message is a separate
run, so a pill records the pixels it covered, and the pixels it drew, in
`/run/baseos-splash-under`. It writes this record only when `/run` is tmpfs,
never on the card. The next pill puts that background back before drawing, so
a narrower message after a wider one leaves no trace of the old pill's ends.
The renderer restores the background only while the screen still shows that
exact pill. It ignores a record for another geometry, or one that something
else has drawn over. The full-screen logo drops the record.

Text uses the bundled Lexend Light font and statically linked FreeType, with a
bitmap-font fallback. After drawing, `FBIOBLANK` and `FBIOPAN_DISPLAY` make the
vendor display driver scan out the framebuffer. `FBSPLASH_TEST` renders PPM
files for offline checks.

## 2. Bootlogo generation

`tools/make-bootlogo.sh <target>` generates `work/<target>/bootlogo.bmp` in the
target's vendor format, a 24-bit, uncompressed, bottom-up BMP at the device's
resolution. `build-image.sh` regenerates the logo and copies it onto p2 without
changing the prepared boot prefix.

### 2.1 Panel rotation

`panel_rotation_ccw` in [devices.json](../devices.json) sets the render
rotation: `90` for RG28XX and `0` for unrotated targets. The renderer supports
`0`, `90`, `180` and `270` degrees.

`fbsplash` reads `BASEOS_PANEL_ROTATION_CCW` from `/etc/baseos-release`, renders
in upright logical coordinates, then rotates in `present()`. The RG28XX uses
640×480 logical coordinates on a 480×640 framebuffer. For pill overlays, the
renderer seeds the logical buffer from the framebuffer to keep the surrounding
artwork. Missing or invalid rotation values default to zero.

Bootlogo generation uses the same rotation. A rotated BMP or test preview looks
sideways in an image viewer and upright on the hardware. Render with rotation
zero to inspect the artwork. `tests/test-splash-rotation.sh` checks full-screen
and pill output pixel for pixel across the supported angles.

### Update scope

`.bosupd` updates replace the inactive rootfs slot, which includes the renderer.
They do not update p2, so a bootlogo change requires reflashing the image or
replacing the boot-resource artwork separately.

## 3. Exceptional states

| condition | pill copy | progress track |
|---|---|---|
| growing + formatting storage | `EXPANDING STORAGE` | boot stage 45 |
| writing + verifying a system update | `UPDATING SYSTEM` | boot stages 50 → 95 |
| system update rejected or unwritable | `UPDATE FAILED` | none |
| restoring the previous system slot | `RESTORING SYSTEM` | none |
| first frontend install | `INSTALLING FRONTEND` | boot stage 85 |
| frontend update | `UPDATING FRONTEND` | boot stage 85 |
| card unavailable | `INSERT SD CARD` | none |
| installer cannot run | `INSTALL FAILED` | none |
| no installed frontend or installer | `ADD FRONTEND TO SD CARD` | none |
| installing the new boot method | `SPEEDING UP BOOT` | none |
| boot areas written but not verified | `BOOT UPDATE FAILED: KEEP POWER ON` | none |
| preparing the USB storage gadget | `STARTING USB STORAGE` | none |
| user storage exported over USB | `USB STORAGE: EJECT BEFORE RESTART` | none |
| USB storage cannot bind | `USB STORAGE FAILED: POWER OFF` | none |

System-update progress advances with bytes written and verified. Stages 50 to
80 cover writing, 80 to 92 cover verification, and 95 is the slot flip. Labels
name the operation or the action the user must take, and omit version numbers.

## 4. Frontend installation

BaseOS shows a static installation pill while the frontend installer runs. It
draws the pill with its own renderer, so the pill does not depend on the
installer's SDL libraries or on the frontend initializing graphics.

## 5. Framebuffer ownership

Every splash draw is synchronous and must finish before the frontend owns the
screen. Do not run background draw loops across the handoff. System updates may
redraw progress synchronously between write and verification chunks, because
they run before the frontend and report measurable progress. Do not animate
progress on a timer.

After the charger fallback accepts a POWER hold, `baseos-splash --charger-boot`
draws `fbsplash 100` once before normal initialization. Idle charging does not
render. See [charger-only boot](11-charger-only-boot.md).
