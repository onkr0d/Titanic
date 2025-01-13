# Use the official Python image as a base
FROM python:3.13.0b4-slim

# Set the working directory inside the container
WORKDIR /app

# Copy only requirements.txt first for caching dependencies
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose port 5000 for Flask
EXPOSE 5000

# Command to run the application
CMD ["python", "app.py"]