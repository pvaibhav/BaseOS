# Product

## Register

product

## Users

Owners of Anbernic RG XX handhelds who want a small, dependable operating system that
boots quickly into their chosen frontend. They see BaseOS mostly during the
few seconds between power-on and frontend hand-off, or while resolving a missing-card
or missing-frontend state on a compact embedded display.

## Product Purpose

BaseOS owns the H700 hardware and boot contract while remaining independent of any
frontend. Success means a reproducible image, a calm and intelligible boot, reliable
hardware behavior, and an unobtrusive hand-off to NextUI or another installed
frontend.

## Brand Personality

Quiet, precise, reassuring. BaseOS should feel like dependable system software: clear
when action is required, otherwise visually restrained and quick to disappear.

## Anti-references

- Frontend-specific branding presented as the operating-system identity.
- Decorative boot animation, noisy diagnostics, version badges, or progress that does
  not correspond to real system state.
- Generic desktop or mobile UI transplanted onto a small handheld display.
- Ambiguous empty states that describe a problem without telling the owner what to do.

## Design Principles

1. Make every visible boot transition correspond to real progress.
2. Keep BaseOS neutral and let the installed frontend own the lasting experience.
3. Use concise, actionable language for states that require user intervention.
4. Preserve one visual identity across bootloader, userspace boot, and hand-off.
5. Design for the smallest supported display first, then verify every native geometry
   **and orientation**. A panel's dimensions do not tell you which way it is mounted.

## Accessibility & Inclusion

Maintain readable contrast and sizing across 480×640, 640×480, 720×480, and
720×720 panels, in the orientation the device is actually held. The 480×640
RG28XX is a landscape device. Do not rely on color alone to communicate status. Pair
illumination with plain-language messages when user action is required. Avoid unnecessary motion
and flashing during boot.
