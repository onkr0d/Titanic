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

# Copy application source
COPY . .


# Runtime Stage 
FROM python:alpine
WORKDIR /app

# Install only runtime dependencies
RUN apk add --no-cache \
    ffmpeg \
    redis

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy Firebase credentials
COPY --from=builder /app/admin-sdk-cred.json ./admin-sdk-cred.json
RUN chmod 600 admin-sdk-cred.json

# Copy application code
COPY --from=builder /app .

# Ensure startup script is executable
RUN chmod +x start.sh

# Add pip-installed binaries to PATH
ENV PATH=/usr/local/bin:$PATH

# Expose ports
EXPOSE 5000 6379

# Launch entrypoint
CMD ["./start.sh"]