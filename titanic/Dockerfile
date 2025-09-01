# ---------- builder ----------
FROM --platform=$BUILDPLATFORM rust:1.88 AS builder
ARG BUILDARCH
ARG TARGETARCH
ENV ZIG_VERSION=0.13.0

# 1) Install Zig for the *builder* arch, with a mirror fallback
#    (wget/ curl will fail with 8 if ziglang.org 4xx/5xx's us)
RUN set -eux; \
    apt-get update && apt-get install -y wget xz-utils ca-certificates; \
    case "$BUILDARCH" in \
        amd64)  ZIG_ARCH="x86_64" ;; \
        arm64)  ZIG_ARCH="aarch64" ;; \
        *) echo "Unsupported BUILDARCH=$BUILDARCH" >&2; exit 1 ;; \
    esac; \
    URL="https://ziglang.org/download/${ZIG_VERSION}/zig-linux-${ZIG_ARCH}-${ZIG_VERSION}.tar.xz"; \
    ALT="https://sourceforge.net/projects/asc-downloads/files/zig-${ZIG_VERSION}-cf90dfd-20240607/zig-linux-${ZIG_ARCH}-${ZIG_VERSION}.tar.xz/download"; \
    (wget -qO /tmp/zig.tar.xz "$URL" || wget -qO /tmp/zig.tar.xz "$ALT"); \
    tar -C /opt -xJf /tmp/zig.tar.xz; \
    ln -sf "/opt/zig-linux-${ZIG_ARCH}-${ZIG_VERSION}/zig" /usr/local/bin/zig; \
    cargo install --locked cargo-zigbuild
# (Zig 0.13 file names match this pattern; see Zig downloads.) :contentReference[oaicite:3]{index=3}

WORKDIR /app
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src && echo "fn main() {}" > src/main.rs

# 2) Pick the *target* triple deterministically
RUN case "$TARGETARCH" in \
        amd64)  TARGET_TRIPLE="x86_64-unknown-linux-musl" ;; \
        arm64)  TARGET_TRIPLE="aarch64-unknown-linux-musl" ;; \
        *) echo "Unsupported TARGETARCH=$TARGETARCH" >&2; exit 1 ;; \
    esac && \
    rustup target add "$TARGET_TRIPLE" && \
    cargo zigbuild --release --target "$TARGET_TRIPLE"

# Build the real app
RUN rm -f src/main.rs
COPY src/ src/
RUN case "$TARGETARCH" in \
        amd64)  TARGET_TRIPLE="x86_64-unknown-linux-musl" ;; \
        arm64)  TARGET_TRIPLE="aarch64-unknown-linux-musl" ;; \
        *) echo "Unsupported TARGETARCH=$TARGETARCH" >&2; exit 1 ;; \
    esac && \
    cargo zigbuild --release --target "$TARGET_TRIPLE"

# ---------- runtime ----------
FROM alpine:3.20
RUN apk add --no-cache ca-certificates libc6-compat
WORKDIR /app

# Copy the compiled binary regardless of target triple directory
COPY --from=builder /app/target/*/release/titanic /usr/local/bin/titanic
RUN chmod +x /usr/local/bin/titanic

# (optional) drop privileges
RUN addgroup -g 1001 -S titanic && adduser -S titanic -u 1001
USER 1001:1001

EXPOSE 3029
ENTRYPOINT ["/usr/local/bin/titanic"]
