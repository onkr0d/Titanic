# Build stage - use Debian for better cross-compilation support
FROM --platform=$BUILDPLATFORM rust:1.88 AS builder

# Enable multi-platform arguments
ARG TARGETPLATFORM
ARG BUILDPLATFORM

# Install Zig for cross-compilation
RUN apt-get update && apt-get install -y \
    wget \
    xz-utils \
    && mkdir -p /opt \
    && ARCH=$(uname -m | sed 's/aarch64/aarch64/;s/x86_64/x86_64/') \
    && wget https://ziglang.org/download/0.13.0/zig-linux-${ARCH}-0.13.0.tar.xz \
    && tar -xf zig-linux-${ARCH}-0.13.0.tar.xz \
    && mv zig-linux-${ARCH}-0.13.0 /opt/zig \
    && ln -s /opt/zig/zig /usr/local/bin/zig \
    && rm zig-linux-${ARCH}-0.13.0.tar.xz

# Install cargo-zigbuild for easy cross-compilation
RUN cargo install cargo-zigbuild

WORKDIR /app

# Add both targets
RUN rustup target add x86_64-unknown-linux-musl aarch64-unknown-linux-musl

# Copy Cargo files
COPY Cargo.toml Cargo.lock* ./

# Create a dummy main.rs to build dependencies
RUN mkdir src && echo "fn main() {}" > src/main.rs

# Build dependencies with zigbuild based on target platform
RUN case "$TARGETPLATFORM" in \
        "linux/amd64") cargo zigbuild --release --target x86_64-unknown-linux-musl ;; \
        "linux/arm64") cargo zigbuild --release --target aarch64-unknown-linux-musl ;; \
        *) echo "Unsupported platform: $TARGETPLATFORM" && exit 1 ;; \
    esac

# Remove dummy main.rs and copy actual source
RUN rm src/main.rs
COPY src/ src/

# Build the application with zigbuild
RUN case "$TARGETPLATFORM" in \
        "linux/amd64") cargo zigbuild --release --target x86_64-unknown-linux-musl ;; \
        "linux/arm64") cargo zigbuild --release --target aarch64-unknown-linux-musl ;; \
        *) echo "Unsupported platform: $TARGETPLATFORM" && exit 1 ;; \
    esac

# Runtime stage
FROM alpine:latest

# Install runtime dependencies
RUN apk add --no-cache \
    ca-certificates \
    libc6-compat

# Create non-root user
RUN addgroup -g 1001 -S titanic && \
    adduser -S titanic -u 1001

WORKDIR /app

# Copy binary from builder based on platform (match musl targets deterministically)
ARG TARGETPLATFORM
COPY --from=builder /app/target/*-unknown-linux-musl/release/titanic /app/titanic

# Create media directory and set permissions
RUN mkdir -p /downloads && \
    chown -R titanic:titanic /app /downloads

# Switch to non-root user
USER titanic

# Expose port
EXPOSE 3029

# Ensure the binary is executable and visible in logs at build time
RUN test -f /app/titanic && chmod +x /app/titanic && ls -l /app/titanic

# Run the application
ENTRYPOINT ["/app/titanic"]