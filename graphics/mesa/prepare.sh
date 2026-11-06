#!/bin/sh
set -eu
umask 022
export PYTHONDONTWRITEBYTECODE=1
apt-get update -qq
xargs apt-get install -y --no-install-recommends < /src/graphics/mesa/container-packages.lock
pip3 install -r /src/graphics/mesa/python-packages.lock
w=/src/work/mainline/graphics
cmake -S "$w/source/SPIRV-Tools-2024.1" -B "$w/build-spirv" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/spirv -DSPIRV-Headers_SOURCE_DIR="$w/source/SPIRV-Headers-8b246ff75c6615ba4532fe4fde20f1be090c3764" -DSPIRV_SKIP_TESTS=ON -DSPIRV_SKIP_EXECUTABLES=ON
cmake --build "$w/build-spirv" -j6
cmake --install "$w/build-spirv"
dpkg-query -W > "$w/container-packages.tsv"
pip3 freeze > "$w/python-packages.txt"
