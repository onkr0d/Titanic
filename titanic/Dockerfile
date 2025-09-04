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

# Copy manifest files
COPY Cargo.toml Cargo.lock ./

# Copy source code
COPY src ./src

# Build the application in release mode
RUN cargo build --release

# Runtime stage
FROM alpine:latest

# Install runtime dependencies
RUN apk add --no-cache \
    ca-certificates \
    openssl

# Create a non-root user
RUN addgroup -g 1001 -S titanic && \
    adduser -u 1001 -S titanic -G titanic

# Create data directory
RUN mkdir -p /data/media && \
    chown -R titanic:titanic /data

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
