# Build stage
FROM rust:1.88-alpine AS builder

# Install build dependencies
RUN apk add --no-cache \
    musl-dev \
    pkgconfig \
    openssl-dev

WORKDIR /app

# Copy Cargo files
COPY Cargo.toml Cargo.lock* ./

# Create a dummy main.rs to build dependencies
RUN mkdir src && echo "fn main() {}" > src/main.rs

# Build dependencies
RUN cargo build --release

# Remove dummy main.rs and copy actual source
RUN rm src/main.rs
COPY src/ src/

# Build the application
RUN cargo build --release

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

# Copy binary from builder
COPY --from=builder /app/target/release/titanic /app/titanic

# Create media directory and set permissions
RUN mkdir -p /downloads && \
    chown -R titanic:titanic /downloads

# Switch to non-root user
USER titanic

# Expose port
EXPOSE 3029

# Run the application
CMD ["./titanic"] 