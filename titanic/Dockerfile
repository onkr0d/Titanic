# Build stage
FROM rust:1.82-alpine AS builder

# Install required packages for building
RUN apk add --no-cache \
    musl-dev \
    openssl-dev \
    pkgconfig \
    perl \
    make

# Create app directory
WORKDIR /app

# Copy manifest files first (for better caching)
COPY Cargo.toml Cargo.lock ./

# Create a dummy main.rs to build dependencies (this gets cached)
RUN mkdir -p src && echo "fn main() {}" > src/main.rs

# Build dependencies only (cached if Cargo.toml/Cargo.lock don't change)
RUN cargo build --release

# Remove dummy source and binary to force rebuild
RUN rm -rf src target/release/deps/titanic* target/release/titanic

# Copy actual source code
COPY src ./src

# Build the actual application
RUN cargo build --release

# Runtime stage
FROM alpine:latest

# Install runtime dependencies
RUN apk add --no-cache \
    ca-certificates \
    openssl

# Create a non-root user
RUN addgroup -g 1000 -S titanic && \
    adduser -u 1000 -S titanic -G titanic

# Create data directory (for app data, not media files)
RUN mkdir -p /data && \
    chown -R titanic:titanic /data

# Note: /downloads will be mounted from host, so we don't create it here

# Copy the binary from builder stage
COPY --from=builder /app/target/release/titanic /usr/local/bin/titanic

# Switch to non-root user
USER titanic

# Expose port
EXPOSE 3029

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3029/health || exit 1

# Set the default command
CMD ["titanic"]
