FROM debian:bullseye AS sysroot
RUN printf "deb http://archive.debian.org/debian bullseye main\ndeb http://archive.debian.org/debian-security bullseye-security main\n" > /etc/apt/sources.list && rm -f /etc/apt/sources.list.d/* && dpkg --add-architecture arm64 && apt-get update && apt-get install -y --no-install-recommends \
      libc6-dev:arm64 libgcc-10-dev:arm64 libstdc++-10-dev:arm64 linux-libc-dev:arm64 \
      zlib1g-dev:arm64 libudev-dev:arm64 libexpat1-dev:arm64 && \
    rm -rf /var/lib/apt/lists/*

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git python3 python3-mako python3-packaging python3-yaml python3-pip \
      bison flex pkg-config build-essential cmake ninja-build meson glslang-tools \
      gcc-aarch64-linux-gnu g++-aarch64-linux-gnu zlib1g-dev libexpat1-dev \
      llvm-18-dev libclang-18-dev clang-18 libclc-18-dev libllvmspirvlib-18-dev llvm-spirv-18 libpolly-18-dev && \
    rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages "meson>=1.4,<1.8"
COPY --from=sysroot /usr/include /sysroot/usr/include
COPY --from=sysroot /usr/lib/aarch64-linux-gnu /sysroot/usr/lib/aarch64-linux-gnu
COPY --from=sysroot /lib/aarch64-linux-gnu /sysroot/lib/aarch64-linux-gnu
COPY --from=sysroot /usr/lib/gcc/aarch64-linux-gnu /sysroot/usr/lib/gcc/aarch64-linux-gnu
RUN mv /usr/aarch64-linux-gnu/include/c++/13 /sysroot/usr/include/c++/13 && rm -rf /usr/aarch64-linux-gnu/include /usr/aarch64-linux-gnu/lib && \
    ln -s /sysroot/usr/include /usr/aarch64-linux-gnu/include && \
    ln -s /sysroot/usr/lib/aarch64-linux-gnu /usr/aarch64-linux-gnu/lib
RUN find /sysroot -type l -lname "/*" | while read -r l; do ln -sfn "/sysroot$(readlink "$l")" "$l"; done
RUN ln -s aarch64-linux-gnu/ld-2.31.so /sysroot/lib/ld-linux-aarch64.so.1 && test -e /sysroot/lib/ld-linux-aarch64.so.1
