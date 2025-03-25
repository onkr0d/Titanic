# Use the official Python image as a base
FROM python:alpine

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies including Redis
RUN apk add --no-cache \
    redis \
    ffmpeg

# Copy only requirements.txt first for caching dependencies
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Make the startup script executable
RUN chmod +x start.sh

# Expose ports for Flask and Redis
EXPOSE 5000 6379

# Run the startup script
CMD ["./start.sh"]