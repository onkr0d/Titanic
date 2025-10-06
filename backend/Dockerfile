# Builder Stage 
FROM python:alpine AS builder

# Set working dir for building
WORKDIR /app

# Install only build-time dependencies
RUN apk add --no-cache \
    build-base \
    libffi-dev \
    openssl-dev \
    python3-dev

# Copy and install Python packages into /install
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# Copy only the application source files needed
COPY app.py .
COPY hypercorn.toml .
COPY start.sh .
COPY jobs/ jobs/


# Runtime Stage 
FROM python:alpine
WORKDIR /app

# Install only runtime dependencies
RUN apk add --no-cache \
    ffmpeg \
    redis \
    curl \
    wget

# Download RNNoise model for audio processing
RUN mkdir -p /app/models && \
    wget -O /app/models/rnnoise-model.rnnn \
    https://github.com/GregorR/rnnoise-models/raw/master/somnolent-hogwash-2018-09-01/sh.rnnn || \
    echo "Warning: Failed to download RNNoise model, audio noise reduction will be skipped"

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --from=builder /app/app.py .
COPY --from=builder /app/hypercorn.toml .
COPY --from=builder /app/start.sh .
COPY --from=builder /app/jobs/ jobs/

# Copy Redis configuration
COPY redis.conf .

# Create a non-root user
RUN addgroup -g 1000 -S flask && \
    adduser -u 1000 -S flask -G flask

# Create Redis data directory with proper permissions
RUN mkdir -p /app/redis-data && \
    chown -R flask:flask /app/redis-data

# Note: Don't create videos directories here - they're created at runtime
# and the volume mount will override any directories created during build

# Ensure startup script is executable
RUN chmod u+x start.sh

# Add pip-installed binaries to PATH
ENV PATH=/usr/local/bin:$PATH

# Switch to non-root user
USER flask

# Expose ports
EXPOSE 5000 6379

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Launch entrypoint
CMD ["sh", "./start.sh"]