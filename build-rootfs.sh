#!/bin/sh
# Assemble one target's minimal base OS rootfs from:
#   work/<target>/stock-harvest.tar  (prepare-stock.sh)
#   work/tools/                      (build-tools.sh: busybox, dropbearmulti)
#   overlay/                (our init + config)
# Output: work/<target>/rootfs.tar + closure report.
# Everything that must execute aarch64 code (busybox --install, ldconfig,
# ld.so closure verification) runs in a linux/arm64 container (native on Apple
# Silicon; QEMU on Intel hosts).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"
TARGET="${1:?usage: $0 <target>}"
[ "$#" -eq 1 ] || { echo "usage: $0 <target>" >&2; exit 2; }
eval "$(python3 "$HERE/tools/device_profile.py" shell "$TARGET")"
WORK="$HERE/work/$TARGET"
TOOLS="$HERE/work/tools"

[ -f "$WORK/source.json" ] || { echo "missing $WORK/source.json (run prepare-stock.sh $TARGET IMAGE)"; exit 1; }
[ -f "$WORK/stock-harvest.tar" ] || { echo "missing $WORK/stock-harvest.tar (run prepare-stock.sh $TARGET IMAGE)"; exit 1; }
for tool in busybox dropbearmulti curl fbsplash gptgrow gptslot sftp-server adbd axp-off charger-wait avahi-daemon boot-clock native-health baseos-regs e2fsck; do
  [ -x "$TOOLS/$tool" ] || { echo "missing $TOOLS/$tool (run build-tools.sh)"; exit 1; }
done
BASEOS_VERSION="$(tr -d ' \n' < "$HERE/VERSION")"
[ -n "$BASEOS_VERSION" ] || { echo "VERSION is empty"; exit 1; }
BASEOS_BUILD="$(git -C "$HERE" describe --always --dirty 2>/dev/null || echo unknown)"
[ -f "$TOOLS/ca-certificates.crt" ] \
  || { echo "missing $TOOLS/ca-certificates.crt (run build-tools.sh)"; exit 1; }
# Refuse to ship binaries older than the sources in this checkout. A stale
# fbsplash paired with current overlay scripts is invisible until it renders
# wrongly on a device, so fail here rather than at boot.
"$HERE/tools/tools-stamp.sh" | cmp -s - "$TOOLS/.stamp" || {
  echo "$TOOLS is stale for the current sources (run build-tools.sh)" >&2; exit 1; }
python3 "$HERE/tools/source_manifest.py" verify "$WORK/source.json" "$TARGET"
python3 "$HERE/tools/verify_harvest.py" "$WORK/stock-harvest.tar" \
  "$HERE/manifest/harvest.list" "$HERE/devices.json" "$TARGET"
python3 "$HERE/tools/kernel_gzip.py" prepare "$TARGET" "$WORK/boot-prefix.img" "$WORK/boot-gzip"

# Every released rootfs must contain the module built for this exact vendor kernel.
"$HERE/build-audio-module.sh" "$TARGET"

docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_AARCH64" \
  -v "$WORK":/work -v "$TOOLS":/tools:ro \
  -v "$HERE/tools/strip_gpu_module.py":/build/strip_gpu_module.py:ro \
  -v "$HERE/overlay":/overlay:ro -v "$HERE/assets":/assets:ro \
  -e BASEOS_TARGET="$PROFILE_TARGET" \
  -e BASEOS_DEVICE="$PROFILE_BASEOS_DEVICE" \
  -e BASEOS_MODEL="$PROFILE_MODEL" \
  -e BASEOS_MODEL_STRING="$PROFILE_MODEL_STRING" \
  -e BASEOS_PANEL_ROTATION_CCW="$PROFILE_PANEL_ROTATION_CCW" \
  -e BASEOS_WIFI="$PROFILE_WIFI" \
  -e BASEOS_BLUETOOTH="$PROFILE_BLUETOOTH" \
  -e BASEOS_VERSION="$BASEOS_VERSION" \
  -e BASEOS_BUILD="$BASEOS_BUILD" \
  alpine:3.20 sh -euc '
  # Build tools remain outside the assembled rootfs. Native AArch64 binutils
  # can strip the vendor module without changing its loadable code or ABI.
  apk add --no-cache binutils python3 >/dev/null
  R=/tmp/rootfs
  rm -rf "$R"; mkdir -p "$R"

  ## 1. FHS skeleton with Ubuntu merged-usr symlinks (harvest paths assume it)
  mkdir -p "$R/usr/bin" "$R/usr/sbin" "$R/usr/lib" "$R/usr/libexec" "$R/usr/share"
  ln -s usr/bin "$R/bin"; ln -s usr/sbin "$R/sbin"; ln -s usr/lib "$R/lib"
  mkdir -p "$R/proc" "$R/sys" "$R/dev" "$R/tmp" "$R/run" "$R/var" "$R/data" \
           "$R/root" "$R/mnt/sdcard" "$R/mnt/system" "$R/mnt/vendor/bin" "$R/mnt/vendor/ctrl" \
           "$R/etc/init.d" "$R/media"
  chmod 700 "$R/root"; chmod 1777 "$R/tmp"

  ## 2. BusyBox first (harvest + overlay override applets where they overlap)
  cp /tools/busybox "$R/usr/bin/busybox"
  chroot "$R" /usr/bin/busybox --install -s
  # /init comes from the overlay as a regular staged-marker script (kernel
  # cmdline is init=/init; the vendor initramfs switch_roots into it)

  ## 3. Stock harvest on top
  tar -xf /work/stock-harvest.tar -C "$R"
  # This alias is fixed; bake it in instead of recreating it on every boot.
  ln -sfn /mnt/sdcard "$R/mnt/SDCARD"
  # Keep the prepared stock harvest pristine. Only remove GPU debug data from
  # this build copy, and fail rather than ship a changed module ABI/signature.
  GPU="$R/usr/lib/modules/mali_kbase.ko"
  [ -f "$GPU" ] || { echo "FATAL: missing harvested GPU module" >&2; exit 1; }
  python3 /build/strip_gpu_module.py "$GPU" "$GPU.strip-debug" \
    --report /work/gpu-strip-report.json
  mv "$GPU.strip-debug" "$GPU"
  # alsa-lib plugin dir: drop static/libtool litter
  rm -f "$R"/usr/lib/aarch64-linux-gnu/alsa-lib/*.a "$R"/usr/lib/aarch64-linux-gnu/alsa-lib/*.la
  # The build is mandatory: do not silently ship only its userspace loader.
  install -m 644 /work/audio-module/h700_speaker_amp.ko "$R/usr/lib/modules/h700_speaker_amp.ko"
  # Starts the USB host controllers that the boot files hold back; see rcS.
  install -m 644 /work/audio-module/h700_late_usb.ko "$R/usr/lib/modules/h700_late_usb.ko"

  # flat module paths used by rcS / setBluetooth.sh
  [ ! -f "$R/usr/lib/modules/4.9.170/kernel/drivers/net/wireless/rtl8821cs/8821cs.ko" ] \
    || ln -sf 4.9.170/kernel/drivers/net/wireless/rtl8821cs/8821cs.ko "$R/usr/lib/modules/8821cs.ko"
  [ ! -f "$R/usr/lib/modules/4.9.170/kernel/drivers/bluetooth/rtl_btlpm.ko" ] \
    || ln -sf 4.9.170/kernel/drivers/bluetooth/rtl_btlpm.ko "$R/usr/lib/modules/rtl_btlpm.ko"
  # ELF interpreter compat path: every dynamic binary requests
  # /lib/ld-linux-aarch64.so.1 (resolves via the /lib -> usr/lib symlink)
  ln -sf aarch64-linux-gnu/ld-linux-aarch64.so.1 "$R/usr/lib/ld-linux-aarch64.so.1"
  # dev-name GLES symlinks (launch.sh probes libEGL.so before libEGL.so.1)
  ln -sf libEGL.so.1 "$R/usr/lib/libEGL.so"
  ln -sf libGLESv2.so.2 "$R/usr/lib/libGLESv2.so"

  ## 4. Our overlay wins over everything
  # `busybox --install` symlinked /usr/sbin/poweroff and /usr/sbin/reboot at the
  # busybox binary, and /sbin is itself a symlink to usr/sbin. Copying our shims
  # onto those symlinks would follow them and overwrite busybox, so drop them
  # first — the same hazard the fbsplash applet has further down.
  rm -f "$R/usr/sbin/poweroff" "$R/usr/sbin/reboot"
  cp -R /overlay/. "$R/"
  # Target identity is generated here; the overlay contains no device-specific
  # model data. BASEOS_DEVICE is the frontend family, while BASEOS_TARGET is
  # the exact hardware build target. BASEOS_PANEL_ROTATION_CCW is read by
  # fbsplash, which ships as one binary for every target.
  {
    printf "%s\n" "# Generated by build-rootfs.sh; do not edit in the image."
    printf "BASEOS=1\nBASEOS_PLATFORM=h700\n"
    printf "BASEOS_VERSION=%s\n" "$BASEOS_VERSION"
    printf "BASEOS_BUILD=%s\n" "$BASEOS_BUILD"
    printf "BASEOS_TARGET=%s\n" "$BASEOS_TARGET"
    printf "BASEOS_DEVICE=%s\n" "$BASEOS_DEVICE"
    printf "BASEOS_MODEL=\"%s\"\n" "$BASEOS_MODEL"
    printf "BASEOS_MODEL_STRING=%s\n" "$BASEOS_MODEL_STRING"
    printf "BASEOS_PANEL_ROTATION_CCW=%s\n" "$BASEOS_PANEL_ROTATION_CCW"
    printf "BASEOS_WIFI=%s\nBASEOS_BLUETOOTH=%s\n" "$BASEOS_WIFI" "$BASEOS_BLUETOOTH"
  } > "$R/etc/baseos-release"
  # os-release carries the same version so it cannot drift from the identity
  # baseos-update compares payloads against.
  {
    printf "NAME=\"Base OS\"\nID=baseos\n"
    printf "VERSION_ID=%s\n" "$BASEOS_VERSION"
    printf "PRETTY_NAME=\"Base OS %s (h700)\"\n" "$BASEOS_VERSION"
  } > "$R/etc/os-release"
  mkdir -p "$R/mnt/vendor/bin"
  printf "%s\n" "$BASEOS_MODEL_STRING" > "$R/mnt/vendor/bin/dmenu.bin"
  chmod 755 "$R/init" \
            "$R/etc/init.d/rcS" "$R/etc/init.d/rcK" "$R/etc/init.d/dev" \
            "$R/usr/bin/baseos-splash" "$R/usr/bin/timedatectl" \
            "$R/usr/sbin/baseos-ntp" "$R/usr/sbin/baseos-ntp-notify" \
            "$R/usr/sbin/frontend-session" "$R/usr/sbin/systemctl" \
            "$R/usr/sbin/expand-storage" "$R/usr/sbin/baseos-update" \
            "$R/usr/sbin/boot-menu-held" "$R/usr/sbin/baseos-charger" \
            "$R/usr/sbin/baseos-poweroff" "$R/usr/sbin/baseos-reboot" \
            "$R/usr/sbin/poweroff" "$R/usr/sbin/reboot" \
            "$R/usr/sbin/baseos-config" "$R/usr/sbin/baseos-mdns" "$R/usr/sbin/h700-speaker-amp-load" \
            "$R/usr/sbin/baseos-startup-sound" \
            "$R/usr/sbin/usb-gadget-adb" "$R/usr/sbin/usb-storage-mode" \
            "$R/mnt/vendor/ctrl/setBluetooth.sh" \
            "$R/usr/share/udhcpc/default.script"
  mkdir -p "$R/usr/share/baseos"
  mv "$R/etc/shadow" "$R/usr/share/baseos/shadow"
  chmod 600 "$R/usr/share/baseos/shadow"
  ln -s /run/shadow "$R/etc/shadow"
  # Guard: every boot-critical script must be executable (a non-exec script is
  # skipped by its `[ -x ]` guard and fails silently — cost us one flash).
  for s in /init /etc/init.d/rcS /etc/init.d/rcK /usr/sbin/frontend-session \
           /usr/bin/baseos-splash /usr/bin/timedatectl \
           /usr/sbin/baseos-ntp /usr/sbin/baseos-ntp-notify \
           /usr/sbin/expand-storage /usr/sbin/baseos-update /usr/sbin/systemctl \
           /usr/sbin/boot-menu-held /usr/sbin/baseos-charger \
           /usr/sbin/baseos-poweroff /usr/sbin/baseos-reboot \
           /usr/sbin/poweroff /usr/sbin/reboot \
           /usr/sbin/baseos-config /usr/sbin/baseos-mdns /usr/sbin/h700-speaker-amp-load \
           /usr/sbin/baseos-startup-sound \
           /usr/sbin/usb-gadget-adb /usr/sbin/usb-storage-mode \
           /mnt/vendor/ctrl/setBluetooth.sh; do
    [ -x "$R$s" ] || { echo "FATAL: $s is not executable in rootfs"; exit 1; }
  done

  ## 5. Runtime-generated files live in /run; bake the symlinks
  ln -sf /run/resolv.conf "$R/etc/resolv.conf"
  ln -sf /run/machine-id "$R/etc/machine-id"
  ln -sf /run/localtime "$R/etc/localtime"
  ln -sf /proc/self/mounts "$R/etc/mtab"
  # Hostname and hosts are generated by baseos-config before services start.
  ln -sf /run/hosts "$R/etc/hosts"
  ln -sf /run/hostname "$R/etc/hostname"

  ## 6. Dropbear + SFTP (dev flavour)
  cp /tools/dropbearmulti "$R/usr/sbin/dropbearmulti"
  for n in dropbear dropbearkey dbclient scp; do
    ln -sf dropbearmulti "$R/usr/sbin/$n"
  done
  ln -sf ../sbin/dropbearmulti "$R/usr/bin/scp"
  # dropbear 2024.85 compiles its default SFTPSERVER_PATH as
  # /usr/libexec/sftp-server; installing the binary there makes sftp / Forklift /
  # scp work with zero dropbear config changes.
  cp /tools/sftp-server "$R/usr/libexec/sftp-server"
  chmod 755 "$R/usr/libexec/sftp-server"

  ## 6a. Static curl + CA roots for the NextUI RetroAchievements HTTP client.
  cp /tools/curl "$R/usr/bin/curl"
  chmod 755 "$R/usr/bin/curl"
  mkdir -p "$R/etc/ssl/certs"
  cp /tools/ca-certificates.crt "$R/etc/ssl/certs/ca-certificates.crt"

  ## 6b. fbsplash boot splash (static) + its bundled Lexend font
  if [ -f /tools/fbsplash ]; then
    # Contract check on the actual binary being shipped: it must reject
    # option-shaped arguments (exit 2) rather than let atoi() read them as a
    # progress value. There is no /dev/fb0 here, so a well-formed call exits 1
    # instead -- which is what distinguishes the two paths without a panel.
    rc=0; /tools/fbsplash --anything 45 CHECK >/dev/null 2>&1 || rc=$?
    [ "$rc" = 2 ] \
      || { echo "FATAL: fbsplash does not reject options (exit $rc)"; exit 1; }
    rc=0; /tools/fbsplash 45 CHECK >/dev/null 2>&1 || rc=$?
    [ "$rc" = 1 ] \
      || { echo "FATAL: fbsplash rejected a well-formed call (exit $rc)"; exit 1; }
    # busybox ships an fbsplash applet; drop its symlinks so ours is the only
    # fbsplash on PATH (usr/sbin precedes usr/bin), then install the real one.
    rm -f "$R/usr/sbin/fbsplash" "$R/usr/bin/fbsplash"
    cp /tools/fbsplash "$R/usr/bin/fbsplash"
    chmod 755 "$R/usr/bin/fbsplash"
    mkdir -p "$R/usr/share/baseos"
    cp /assets/boot.ttf "$R/usr/share/baseos/boot.ttf"
    [ -f /assets/OFL.txt ] && cp /assets/OFL.txt "$R/usr/share/baseos/boot.ttf.OFL.txt"
  fi
  # gptgrow: first-boot expand-to-fill of the FAT data partition.
  [ -f /tools/gptgrow ] && cp /tools/gptgrow "$R/usr/sbin/gptgrow" && chmod 755 "$R/usr/sbin/gptgrow"
  # gptslot: A/B root-slot geometry and flip, used by baseos-update.
  [ -f /tools/gptslot ] && cp /tools/gptslot "$R/usr/sbin/gptslot" && chmod 755 "$R/usr/sbin/gptslot"
  # Required tools for charger-only boots and safe PMIC shutdown.
  for tool in axp-off charger-wait; do
    cp "/tools/$tool" "$R/usr/sbin/$tool"
    chmod 755 "$R/usr/sbin/$tool"
  done
  # Shared H700 counter reader; unavailable counters retain uptime-only logs.
  cp /tools/boot-clock "$R/usr/sbin/boot-clock"
  chmod 755 "$R/usr/sbin/boot-clock"
  cp /tools/native-health "$R/usr/sbin/native-health"
  chmod 755 "$R/usr/sbin/native-health"
  # Read-only register view for baseos-diag (RTC pair, watchdog, panel strap).
  cp /tools/baseos-regs "$R/usr/sbin/baseos-regs"
  chmod 755 "$R/usr/sbin/baseos-regs"
  # Root filesystem repair for boots without the vendor initramfs, which used
  # to run its own. rcS calls it only when the filesystem has recorded errors.
  cp /tools/e2fsck "$R/usr/sbin/e2fsck"
  chmod 755 "$R/usr/sbin/e2fsck"
  # Existing updaters install this ordinary rootfs. The new system performs
  # the one-time boot-pair migration before confirming its update trial.
  mkdir -p "$R/usr/share/baseos/boot-gzip"
  cp /work/boot-gzip/boot.img /work/boot-gzip/boot-package.bin \
    /work/boot-gzip/manifest "$R/usr/share/baseos/boot-gzip/"
  # Card settings and README are restored after first-boot FAT expansion.
  mkdir -p "$R/usr/share/baseos"
  [ -f /assets/card-readme.txt ] && cp /assets/card-readme.txt "$R/usr/share/baseos/card-readme.txt"
  cp /assets/baseos.conf "$R/usr/share/baseos/baseos.conf"

  ## 6c. USB gadget access, brought up by usb-gadget-adb (init.d/dev).
  cp /tools/adbd "$R/usr/sbin/adbd"
  chmod 755 "$R/usr/sbin/adbd"

  ## 6d. mDNS responder for <hostname>.local (static, no D-Bus or bundled
  ## service definitions; started by baseos-mdns on DHCP, see docs/05).
  cp /tools/avahi-daemon "$R/usr/sbin/avahi-daemon"
  chmod 755 "$R/usr/sbin/avahi-daemon"

  ## 7. ld.so.cache so the dynamic linker finds the multiarch dir
  chroot "$R" /usr/sbin/ldconfig || chroot "$R" /usr/sbin/ldconfig.real

  ## 8. Verify: every ELF in the tree resolves fully inside the tree
  # rtk_hciattach is the stock ARM32 exception. It needs only the ARMHF
  # interpreter and libc; executing its loader is not portable across all
  # linux/arm64 container hosts, so validate those inputs structurally.
  if [ -f "$R/usr/bin/rtk_hciattach" ]; then
    [ -f "$R/usr/lib/ld-linux-armhf.so.3" ] \
      || { echo "FATAL: ARMHF loader missing for rtk_hciattach"; exit 1; }
    [ -f "$R/usr/lib/arm-linux-gnueabihf/libc.so.6" ] \
      || { echo "FATAL: ARMHF libc missing for rtk_hciattach"; exit 1; }
  fi
  find "$R/usr/bin" "$R/usr/sbin" "$R/usr/libexec" -type f | while read -r f; do
    head -c4 "$f" | grep -q "^.ELF" || continue
    case "$f" in
      */busybox|*/dropbearmulti|*/curl|*/fbsplash|*/gptgrow|*/gptslot|*/ldconfig|*/ldconfig.real|*/rtk_hciattach|*/sftp-server|*/adbd|*/axp-off|*/charger-wait|*/avahi-daemon|*/boot-clock|*/native-health|*/baseos-regs|*/e2fsck) continue ;;
    esac
    if ! chroot "$R" /usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1 --list \
        "${f#"$R"}" 2>/dev/null | grep -q "=>"; then
      echo "UNRESOLVABLE: ${f#"$R"}"
    fi
    chroot "$R" /usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1 --list \
        "${f#"$R"}" 2>/dev/null | grep "not found" \
        | sed "s|^|MISSING for ${f#"$R"}: |" || true
  done > /tmp/closure-report.txt 2>&1 || true
  cp /tmp/closure-report.txt /work/closure-report.txt
  if grep -Eq "MISSING|UNRESOLVABLE" /work/closure-report.txt; then
    echo "=== closure problems ==="; cat /work/closure-report.txt
    exit 1
  else
    echo "closure OK: all ELFs resolve in-tree"
  fi

  ## 9. Static console nodes: PID 1 needs them before devtmpfs is mounted
  ## (covers kernels without CONFIG_DEVTMPFS_MOUNT).
  mknod -m 600 "$R/dev/console" c 5 1
  mknod -m 666 "$R/dev/null" c 1 3

  ## 10. Ownership + pack
  grep -qx "BASEOS_TARGET=$BASEOS_TARGET" "$R/etc/baseos-release"
  # fbsplash treats an absent key as an unrotated panel, so a typo here would
  # ship a sideways splash rather than fail. Assert it landed.
  grep -qx "BASEOS_PANEL_ROTATION_CCW=$BASEOS_PANEL_ROTATION_CCW" "$R/etc/baseos-release"
  grep -qx "$BASEOS_MODEL_STRING" "$R/mnt/vendor/bin/dmenu.bin"
  chown -R 0:0 "$R"
  du -sh "$R"
  tar -C "$R" -cf /work/rootfs.tar .
'

ls -lh "$WORK/rootfs.tar"
echo "closure report: $WORK/closure-report.txt"
if grep -Eq "MISSING|UNRESOLVABLE" "$WORK/closure-report.txt" 2>/dev/null; then
  echo "closure report unexpectedly contains failures" >&2
  exit 1
fi
echo "no closure issues"
