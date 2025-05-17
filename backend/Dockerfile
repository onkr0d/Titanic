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
    curl

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --from=builder /app/app.py .
COPY --from=builder /app/hypercorn.toml .
COPY --from=builder /app/start.sh .
COPY --from=builder /app/jobs/ jobs/

# Create uploads directory
RUN mkdir -p uploads

# Ensure startup script is executable
RUN chmod +x start.sh

# Add pip-installed binaries to PATH
ENV PATH=/usr/local/bin:$PATH

# Expose ports
EXPOSE 5000 6379

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Launch entrypoint
CMD ["./start.sh"]