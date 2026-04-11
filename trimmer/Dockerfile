# Build stage
FROM rust:1.91-alpine AS builder

# Install required packages for building
RUN apk add --no-cache \
    musl-dev \
    pkgconfig

# Create app directory
WORKDIR /app

# Copy manifest files first (for better caching)
COPY Cargo.toml Cargo.lock ./

# Create a dummy main.rs to build dependencies (this gets cached)
RUN mkdir -p src && echo "fn main() {}" > src/main.rs

# Build dependencies only (cached if Cargo.toml/Cargo.lock don't change)
RUN cargo build --release

# Remove dummy source and binary to force rebuild
RUN rm -rf src target/release/deps/trimmer* target/release/trimmer

# Copy actual source code and static assets (needed for include_str! at compile time)
COPY src ./src
COPY static ./static

# Build the actual application
RUN cargo build --release

# Runtime stage
FROM alpine:latest

# Install runtime dependencies
RUN apk add --no-cache \
    ca-certificates \
    ffmpeg

# Create a non-root user
RUN addgroup -g 1000 -S trimmer && \
    adduser -u 1000 -S trimmer -G trimmer

# Create data and downloads directories with proper ownership
RUN mkdir -p /data /downloads && \
    chown -R trimmer:trimmer /data /downloads

# Create static directory
RUN mkdir -p /app/static && chown -R trimmer:trimmer /app

# Copy the binary from builder stage
COPY --from=builder /app/target/release/trimmer /usr/local/bin/trimmer

# Copy static assets
COPY --chown=trimmer:trimmer static /app/static

WORKDIR /app

# Switch to non-root user
USER trimmer

# Expose port
EXPOSE 3030

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3030/health || exit 1

# Set the default command
CMD ["trimmer"]
