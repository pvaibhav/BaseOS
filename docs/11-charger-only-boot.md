# 11. Charger-only boot

On charger-origin boots, BaseOS powers the processor off while the PMIC
keeps charging. It handles this before starting the frontend, GPU,
network services, storage expansion or update trials.

## Boot detection

Early in `rcS`, BaseOS reads
`/sys/class/power_supply/axp2202-battery/boot_mode`. Value `1` selects charging;
`0` continues normal startup. If the value is missing or invalid, the exact
`bootreason=charger` command-line token selects charging. USB presence alone
never selects charging mode.

## Charging and startup

`baseos-charger` handles the charging path:

- Holding MENU keeps USB-storage maintenance startup.
- Creating `/data/no-charger-off` disables charger-only handling.
- Otherwise, the helper checks `/data` and the RTC, records a shutdown stamp,
  and calls `axp-off --now`. The PMIC helper flushes persistent filesystems
  and remounts them read-only before cutting processor power.

A recent shutdown stamp (less than 120 seconds old), a future stamp, or a
failed prerequisite selects the fallback instead of another shutdown attempt.
This prevents power cycling. A quick deliberate cable reinsertion also takes
this path.

## Fallback

The fallback turns off the backlight, selects the `powersave` CPU governor
and waits for a fresh one-second POWER hold. It uses blocking input events,
not suspend. A button already held on entry must be released and pressed again.
If input is missing or a helper fails, the fallback retries every 60 seconds
without starting the frontend.

An accepted hold restores brightness and the governor, lights the power LED,
draws the BaseOS logo once and continues initialization in the same boot,
without waiting for button release. Frontend storage and normal services stay
inactive until then. Charging does not consume or confirm an update trial.

## Validation

`tests/test-poweroff-policy.sh` and `tests/test-axp-off.sh` cover boot routing,
shutdown guards, filesystem cleanup, PMIC operations and POWER-hold handling.
Hardware checks cover charging with the processor off and hold-to-start feedback.
Keep time spent waiting in charging mode separate from
[normal boot measurements](09-boot-profiling.md).
