# ---------- builder ----------
FROM --platform=$BUILDPLATFORM rust:1.88 AS builder
ARG TARGETARCH
ARG BUILDARCH

# zig + cargo-zigbuild
RUN set -eux; \
    apt-get update && apt-get install -y wget xz-utils; \
    case "$BUILDARCH" in \
      amd64) ZIG_ARCH=x86_64 ;; \
      arm64) ZIG_ARCH=aarch64 ;; \
      *) echo "Unsupported BUILDARCH=$BUILDARCH" >&2; exit 1 ;; \
    esac; \
    wget -qO /tmp/zig.tgz "https://ziglang.org/download/0.13.0/zig-linux-$ZIG_ARCH-0.13.0.tar.xz"; \
    tar -C /opt -xf /tmp/zig.tgz || true; \
    ln -sf "/opt/zig-linux-$ZIG_ARCH-0.13.0/zig" /usr/local/bin/zig; \
    cargo install --locked cargo-zigbuild

WORKDIR /app

# Resolve triple from TARGETARCH (what BuildKit sets for the target platform)
# See: Docker build variables docs
# https://docs.docker.com/build/building/variables/
RUN case "$TARGETARCH" in \
        amd64)  echo x86_64-unknown-linux-musl  > /tmp/TARGET_TRIPLE ;; \
        arm64)  echo aarch64-unknown-linux-musl > /tmp/TARGET_TRIPLE ;; \
        *) echo "Unsupported TARGETARCH=$TARGETARCH" >&2; exit 1 ;; \
    esac

# Install std sources and common MUSL targets for cross builds
RUN rustup component add rust-src \
    && rustup target add x86_64-unknown-linux-musl aarch64-unknown-linux-musl

# Cache deps
COPY Cargo.toml Cargo.lock ./
RUN set -eux; triple="$(cat /tmp/TARGET_TRIPLE)"; \
    mkdir -p src; echo "fn main() {}" > src/main.rs; \
    cargo zigbuild --release --target "$triple"

# Build real app
RUN rm -f src/main.rs
COPY src/ src/
RUN set -eux; triple="$(cat /tmp/TARGET_TRIPLE)"; \
    cargo zigbuild --release --target "$triple"; \
    cp "target/$triple/release/titanic" /titanic

# ---------- runtime ----------
FROM alpine:3.20
RUN apk add --no-cache ca-certificates libc6-compat
WORKDIR /app

# Copy the *exact* binary for this arch to a stable path
COPY --from=builder /titanic /usr/local/bin/titanic
RUN chmod +x /usr/local/bin/titanic

# (optional) run as non-root once the binary is in place
RUN addgroup -g 1001 -S titanic && adduser -S titanic -u 1001
USER 1001:1001

EXPOSE 3029
ENTRYPOINT ["/usr/local/bin/titanic"]
