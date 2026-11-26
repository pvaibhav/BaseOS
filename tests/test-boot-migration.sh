#!/bin/sh
# Disposable BusyBox/Linux tests; never invokes a device block writer.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
. "$HERE/tools/docker-platform.sh"
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE":/repo:ro -w /repo alpine:3.20 sh -euc \
  'apk add --no-cache python3 >/dev/null; python3 tests/test-boot-migration.py && python3 tests/test-native-migration.py'
