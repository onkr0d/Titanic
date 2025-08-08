# Build stage
FROM rust:1.88-alpine AS builder

# Install build dependencies for vendored OpenSSL compilation
RUN apk add --no-cache \
    musl-dev \
    pkgconfig \
    perl \
    make \
    gcc

WORKDIR /app

# Add x86_64 musl target for cross-compilation
RUN rustup target add x86_64-unknown-linux-musl

# Copy Cargo files
COPY Cargo.toml Cargo.lock* ./

# Create a dummy main.rs to build dependencies
RUN mkdir src && echo "fn main() {}" > src/main.rs

# Build dependencies for x86_64 target
RUN cargo build --release --target x86_64-unknown-linux-musl

# Remove dummy main.rs and copy actual source
RUN rm src/main.rs
COPY src/ src/

# Build the application for x86_64 target
RUN cargo build --release --target x86_64-unknown-linux-musl

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

# Copy binary from builder (cross-compiled target directory)
COPY --from=builder /app/target/x86_64-unknown-linux-musl/release/titanic /app/titanic

# Create media directory and set permissions
RUN mkdir -p /downloads && \
    chown -R titanic:titanic /downloads

# Switch to non-root user
USER titanic

# Expose port
EXPOSE 3029

# Run the application
CMD ["./titanic"] 