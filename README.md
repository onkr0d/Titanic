# Titanic

Sink files to an Umbrel + Plex media server

## Overview

Titanic is a complete video upload and compression system that allows users to upload videos through a web interface, compress them on a backend server, and automatically upload them to an Umbrel device running Plex.

## Architecture

- **Frontend**: React + Vite web app hosted on Firebase
- **Backend**: Flask API running on Oracle Cloud that handles video compression
- **Umbrel Component**: Rust server running on your Umbrel device that receives compressed videos

## Getting Started (Local Development)

This project includes a unified Docker Compose setup to run the entire backend (Flask API) and the Umbrel component with a single command, featuring hot-reloading for the Rust service.

### Prerequisites
- Docker and Docker Compose
- A Firebase project
- A Firebase service account key file named `admin-sdk-cred.json` placed in the `backend/` directory.

### Setup

1.  **Create an environment file:**
    Create a `.env` file in the project root by copying the example below. This file will be used by `docker compose` to inject environment variables into the services.

    ```bash
    # .env (in the project root)

    # -- Umbrel Service --
    # Required: Your Firebase Project ID
    FIREBASE_PROJECT_ID=your-firebase-project-id
    # Optional: Development mode for Umbrel service
    IS_DEV=true
    # Optional: Plex media path for Umbrel service
    PLEX_MEDIA_PATH=./videos/compressed
    ```
    Replace `your-firebase-project-id` with your actual Firebase project ID.

2.  **Run the services:**
    Use the following command from the project root to build and start all services in detached mode:
    ```bash
    docker compose -f docker-compose.dev.yml up -d --build
    ```

### Services
- **Backend API:** Accessible at `http://localhost:6969`
- **Umbrel Service:** Accessible at `http://localhost:3000`
- **Hot-Reloading:** The Umbrel (Rust) service will automatically restart when you save changes to any file in `umbrel/src/`.

### Frontend Development

For the frontend, you can run the Firebase emulators as intended. The frontend will connect to the backend services running in Docker.
```bash
cd frontend
firebase emulators:start
```

## Production Deployment

### Backend (`backend/`)
The backend is designed to be deployed as a Docker container. See `backend/docker-compose.run.yml` for an example of how to run the pre-built image from `ghcr.io`.

### Umbrel Component (`umbrel/`)
The Umbrel component is intended to be run directly on your Umbrel device.
   ```bash
   cd umbrel
   cp env.example .env
   # Edit .env with your Firebase Project ID and set IS_DEV=false
   ./deploy.sh
   ```

## File Flow

1. User uploads video through web interface.
2. Backend receives the file and enqueues a compression job.
3. FFmpeg compresses the video using the H.265 codec (if not already compressed).
4. The backend uploads the compressed video to the Umbrel component.
5. The Umbrel component saves the video to the Plex media directory.
6. Plex automatically detects and indexes the new video.

## Security

- All endpoints require Firebase JWT authentication.
- File uploads are validated for type and size.
- Filenames are sanitized to prevent path traversal.
- Servers run as non-root users.
