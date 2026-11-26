#!/bin/sh
# Run the health CLI with modeled hardware and actual rcS in a disposable chroot.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
. "$HERE/tools/docker-platform.sh"
[ "$#" -le 1 ] || { echo "usage: $0 [PROPOSED_RCS]" >&2; exit 2; }
RCS=${1:-$HERE/overlay/etc/init.d/rcS}
[ -f "$RCS" ] || { echo "missing rcS: $RCS" >&2; exit 2; }
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
    -v "$HERE":/repo:ro -v "$RCS":/proposed-rcS:ro \
    alpine:3.20 sh -euc '
    apk add -q build-base linux-headers python3 busybox-static
    cc -std=c11 -static -O2 -Wall -Wextra -Werror -I /repo/src /repo/tests/native-health-test.c -o /tmp/native-health-test
    /tmp/native-health-test
    cc -std=c11 -static -O2 -Wall -Wextra -Werror -I /repo/src /repo/src/native-health.c -o /tmp/native-health
    rc=0; /tmp/native-health --check || rc=$?
    [ "$rc" -eq 3 ]
    python3 /repo/tests/native-health-startup.py --rcs /proposed-rcS --helper /tmp/native-health-test
    '
