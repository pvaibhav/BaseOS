#!/bin/sh
# Fetch the pinned Ubuntu packages in debs/packages.list into work/debs:
# their shared libraries and copyright files, staged as a rootfs tree.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
LIST="$HERE/debs/packages.list"
OUT="$HERE/work/debs"
MIRROR=http://ports.ubuntu.com/ubuntu-ports

STAMP="$(sha256sum "$LIST" | cut -d' ' -f1)"
if [ -f "$OUT/.stamp" ] && [ "$(cat "$OUT/.stamp")" = "$STAMP" ]; then
  echo "Reusing packages in $OUT"
  exit 0
fi

rm -rf "$OUT"
mkdir -p "$OUT/cache" "$OUT/root/usr/share/licenses"
grep -v '^#' "$LIST" | while read -r path sum; do
  [ -n "$path" ] || continue
  deb="$OUT/cache/${path##*/}"
  curl -fsSL -o "$deb" "$MIRROR/$path"
  echo "$sum  $deb" | sha256sum -c - >/dev/null || { echo "FATAL: $path checksum mismatch" >&2; exit 1; }
  pkg="$(dpkg-deb -f "$deb" Package)"
  arch="$(dpkg-deb -f "$deb" Architecture)"
  x="$OUT/cache/$pkg-$arch"
  dpkg-deb -x "$deb" "$x"
  (cd "$x" && find lib usr/lib -maxdepth 2 -name '*.so.*' 2>/dev/null \( -type f -o -type l \)) | while read -r so; do
    mkdir -p "$OUT/root/${so%/*}"
    cp -a "$x/$so" "$OUT/root/$so"
  done
  mkdir -p "$OUT/root/usr/share/licenses/$pkg"
  cp "$x/usr/share/doc/$pkg/copyright" "$OUT/root/usr/share/licenses/$pkg/"
  echo "$pkg $arch $(dpkg-deb -f "$deb" Version)"
done
rm -rf "$OUT/cache"
echo "$STAMP" > "$OUT/.stamp"
