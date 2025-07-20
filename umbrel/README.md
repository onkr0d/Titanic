# Titanic Umbrel Component

This is the Umbrel component of the Titanic project - a Rust server that handles video uploads to your Umbrel device running Plex.

## Local Development

For instructions on how to run this service as part of the unified development environment, please see the main [README.md](../../README.md) in the root of this project.

## Production Deployment

TODO

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

# Optional: Server bind address (default: 0.0.0.0:3000)
BIND_ADDRESS=0.0.0.0:3000

# Optional: Plex media directory path (default: /data/media)
PLEX_MEDIA_PATH=/data/media

# Optional: Development mode - bypasses authentication (default: false)
IS_DEV=false
```

### Firebase Setup

1. Get your Firebase Project ID from the Firebase Console
2. Ensure your Firebase project has Authentication enabled
3. The server will automatically fetch public keys from Firebase for JWT verification

## Deployment

### Option 1: Docker Compose (Recommended)

1. Clone this repository to your Umbrel device
2. Navigate to the `umbrel` directory
3. Create the `.env` file with your configuration
4. Run the deployment:

```bash
cd umbrel
docker compose up -d
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
3. **Network Issues**: Verify the server is accessible on port 3000

### Logs

View logs with:
```bash
docker compose logs -f titanic-umbrel
```

### Manual Testing

Test the health endpoint:
```bash
curl http://localhost:3000/health
```

## Development

For development, set `IS_DEV=true` in your `.env` file to bypass authentication checks.