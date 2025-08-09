# Build stage
FROM --platform=$BUILDPLATFORM rust:1.88-alpine AS builder

# Enable multi-platform arguments
ARG TARGETPLATFORM
ARG BUILDPLATFORM

# Install build dependencies for cross-compilation
RUN apk add --no-cache \
    musl-dev \
    pkgconfig \
    perl \
    make \
    gcc \
    clang \
    lld

WORKDIR /app

# Add the appropriate Rust target based on the target platform
RUN case "$TARGETPLATFORM" in \
        "linux/amd64") rustup target add x86_64-unknown-linux-musl ;; \
        "linux/arm64") rustup target add aarch64-unknown-linux-musl ;; \
        *) echo "Unsupported platform: $TARGETPLATFORM" && exit 1 ;; \
    esac

# Configure Cargo for cross-compilation
RUN mkdir -p ~/.cargo && \
    case "$TARGETPLATFORM" in \
        "linux/amd64") \
            echo '[target.x86_64-unknown-linux-musl]' >> ~/.cargo/config.toml && \
            echo 'linker = "clang"' >> ~/.cargo/config.toml && \
            echo 'rustflags = ["-C", "link-arg=-fuse-ld=lld", "-C", "target-feature=+crt-static"]' >> ~/.cargo/config.toml ;; \
        "linux/arm64") \
            echo '[target.aarch64-unknown-linux-musl]' >> ~/.cargo/config.toml && \
            echo 'linker = "clang"' >> ~/.cargo/config.toml && \
            echo 'rustflags = ["-C", "link-arg=-fuse-ld=lld", "-C", "link-arg=--target=aarch64-unknown-linux-musl", "-C", "target-feature=+crt-static"]' >> ~/.cargo/config.toml ;; \
    esac

# Copy Cargo files
COPY Cargo.toml Cargo.lock* ./

# Create a dummy main.rs to build dependencies
RUN mkdir src && echo "fn main() {}" > src/main.rs

# Environment for vendored OpenSSL
ENV OPENSSL_STATIC=1

# Set the target based on platform
RUN case "$TARGETPLATFORM" in \
        "linux/amd64") export CARGO_TARGET=x86_64-unknown-linux-musl ;; \
        "linux/arm64") export CARGO_TARGET=aarch64-unknown-linux-musl ;; \
        *) echo "Unsupported platform: $TARGETPLATFORM" && exit 1 ;; \
    esac && \
    cargo build --release --target $CARGO_TARGET

# Remove dummy main.rs and copy actual source
RUN rm src/main.rs
COPY src/ src/

# Build the application with correct target
RUN case "$TARGETPLATFORM" in \
        "linux/amd64") export CARGO_TARGET=x86_64-unknown-linux-musl ;; \
        "linux/arm64") export CARGO_TARGET=aarch64-unknown-linux-musl ;; \
        *) echo "Unsupported platform: $TARGETPLATFORM" && exit 1 ;; \
    esac && \
    cargo build --release --target $CARGO_TARGET

# Runtime stage
FROM alpine:latest

# Install runtime dependencies
RUN apk add --no-cache \
    ca-certificates \
    libc6-compat \
    openssl \
    libgcc

# Create non-root user
RUN addgroup -g 1001 -S titanic && \
    adduser -S titanic -u 1001

WORKDIR /app

# Copy binary from builder (platform-specific target directory)
ARG TARGETPLATFORM
RUN case "$TARGETPLATFORM" in \
        "linux/amd64") echo "x86_64-unknown-linux-musl" > /tmp/target ;; \
        "linux/arm64") echo "aarch64-unknown-linux-musl" > /tmp/target ;; \
        *) echo "Unsupported platform: $TARGETPLATFORM" && exit 1 ;; \
    esac
COPY --from=builder /app/target /tmp/all-targets
RUN TARGET=$(cat /tmp/target) && \
    cp /tmp/all-targets/$TARGET/release/titanic /app/titanic

# Create media directory and set permissions
RUN mkdir -p /downloads && \
    chown -R titanic:titanic /downloads

# Switch to non-root user
USER titanic

# Expose port
EXPOSE 3029

# Debug: Keep container alive and show what's happening
CMD ["sh", "-c", "echo 'Container starting on platform:'; uname -a; echo 'App directory:'; ls -la /app/; echo 'Binary info:'; file /app/titanic; echo 'Attempting to run titanic...'; ./titanic || (echo 'Binary failed, sleeping...'; sleep 300)"] 