#!/bin/sh
# Build the Vulkan driver (Mesa PanVK on the stock kbase GPU driver, drawing
# to fbdev) and the Vulkan loader into work/vulkan. One build serves every
# target. The build cross-compiles, so it needs an x86-64 Docker host; CI
# builds it there and hands the result to the arm64 image job.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/work/vulkan"

STAMP="$(cd "$HERE" && find vulkan -type f | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1)"
if [ -f "$OUT/.stamp" ] && [ "$(cat "$OUT/.stamp")" = "$STAMP" ]; then
  echo "Reusing Vulkan driver in $OUT"
  exit 0
fi

case "$(uname -m)" in
  x86_64|amd64) ;;
  *) echo "build-vulkan.sh: needs an x86-64 Docker host (or a work/vulkan from CI)" >&2; exit 1 ;;
esac

docker build -t baseos-vulkan "$HERE/vulkan"
rm -rf "$OUT"
mkdir -p "$OUT"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$HERE/vulkan":/src:ro -v "$OUT":/out baseos-vulkan sh /src/build.sh
echo "$STAMP" > "$OUT/.stamp"
