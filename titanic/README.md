# Titanic Umbrel Component

This is the Umbrel component of the Titanic project - a Rust server that handles video uploads to your Umbrel device running Plex.

## Local Development

For instructions on how to run this service as part of the unified development environment, please see the main [README.md](../../README.md) in the root of this project.

## Umbrel Deployment

### Installation

1. **Clone to Umbrel App Store:**
   ```bash
   # On your Umbrel device, clone this repository to the app store directory
   cd /home/umbrel/umbrel/app-stores/getumbrel-umbrel-apps-github-53f74447
   git clone https://github.com/onkr0d/Titanic.git titanic
   cd titanic
   ```

2. **Install via Umbrel:**
   ```bash
   # Install the app using Umbrel's CLI
   umbreld client apps.install.mutate --appId titanic
   ```

   ^ if the above fails, run ```sudo docker pull [image]``` so it's up to date and exists on local machine. also, if its a private container registry you must figure that out yourself

3. **Configure Firebase:**
   - Open the Titanic app in your Umbrel dashboard
   - Enter your Firebase Project ID in the configuration widget
   - The app will automatically start with the provided configuration

### What Umbrel Handles

Umbrel automatically manages:
- Docker container lifecycle
- Network configuration and reverse proxy
- Volume mounting for persistent data
- Health checks and monitoring
- App updates and rollbacks
- Environment variable configuration through the UI

**Note:** For Umbrel deployment, you don't need to create or manage `.env` files. Configuration is handled through the Umbrel dashboard widgets.
 TODO ^ might be wrong

## Features

- **Firebase Authentication**: Verifies JWT tokens from your Firebase project
- **Video Upload**: Receives compressed videos and saves them to your Plex media directory
- **Disk Space Monitoring**: Provides disk space information for the media directory
- **Health Checks**: Docker health checks for monitoring

## Prerequisites

- Umbrel device running umbrelOS
- Plex media server installed on Umbrel
- Docker and Docker Compose available on the Umbrel device

## Configuration

### Environment Variables

Create a `.env` file in the `umbrel` directory by copying the example:

```bash
cp env.example .env
```

Then edit the `.env` file with your configuration:

```bash
# Required: Your Firebase Project ID
FIREBASE_PROJECT_ID=your-firebase-project-id

# Optional: Server bind address (default: 0.0.0.0:3029)
BIND_ADDRESS=0.0.0.0:3029

# Optional: Plex media directory path (default: /data/media)
PLEX_MEDIA_PATH=/data/media

# Optional: Development mode - bypasses authentication (default: false)
IS_DEV=false
```

### Firebase Setup

1. Get your Firebase Project ID from the Firebase Console
2. Ensure your Firebase project has Authentication enabled
3. The server will automatically fetch public keys from Firebase for JWT verification

## Local Development

### Option 1: Docker Development

```bash
cd titanic
cp env.example .env
# Edit .env with your Firebase Project ID and set IS_DEV=true

# Build and run with hot reloading
docker build -f Dockerfile.dev -t titanic-dev .
docker run -d \
  --name titanic-dev \
  -p 3029:3029 \
  -v $(pwd)/src:/app/src \
  -v $(pwd)/Cargo.toml:/app/Cargo.toml \
  -v $(pwd)/Cargo.lock:/app/Cargo.lock \
  -v $(pwd)/media:/data/media \
  --env-file .env \
  titanic-dev
```

### Option 2: Local Rust Development

```bash
cd titanic
cp env.example .env
# Edit .env with your configuration
cargo run
```

### Option 3: Using Root Docker Compose

For development with the entire Titanic stack:

```bash
cd ../  # Go to project root
docker compose -f docker-compose.dev.yml up -d
```

## API Endpoints

### Health Check
```
GET /health
```
Returns server health status.

### Upload Video
```
POST /api/upload
Authorization: Bearer <firebase-jwt-token>
Content-Type: multipart/form-data
```
Uploads a video file to the Plex media directory.

**Form Data:**
- `file`: The video file to upload

**Response:**
```json
{
  "message": "File uploaded successfully",
  "filename": "video_abc123.mp4",
  "plex_path": "/data/media/video_abc123.mp4"
}
```

### Disk Space
```
GET /api/space
Authorization: Bearer <firebase-jwt-token>
```
Returns disk space information for the media directory.

**Response:**
```json
{
  "total": 1000000000000,
  "used": 500000000000,
  "free": 500000000000
}
```

## Security

- All endpoints require Firebase JWT authentication
- Filenames are sanitized to prevent path traversal attacks
- Unique filenames are generated to prevent conflicts
- The server runs as a non-root user

## Monitoring

The container includes health checks that can be monitored by Docker or external monitoring systems.

## Troubleshooting

### Common Issues

1. **Authentication Errors**: Ensure your Firebase Project ID is correct
2. **Permission Errors**: Check that the media directory is writable
3. **Network Issues**: Verify the server is accessible on port 3029

### Logs

View logs with:
```bash
# For Umbrel deployment
docker logs titanic_app_1

# For local development
docker logs titanic-dev
```

### Manual Testing

Test the health endpoint:
```bash
curl http://localhost:3029/health
```

## Development

For development, set `IS_DEV=true` in your `.env` file to bypass authentication checks.