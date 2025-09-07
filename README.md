# Titanic

Sync files to an Umbrel + Plex media server

## Overview

Titanic is a complete video upload and compression system that allows users to upload videos through a webbsite, and see the compressed videos on their Plex (running on Umbrel).

## Architecture

- **Frontend**: React + Vite web app hosted on Firebase
- **Backend**: Flask server running on an Oracle Cloud VPS that handles video compression
- **Umbrel App**: Rust server running on your Umbrel device that receives compressed videos

## Getting Started (Local Development)

This project includes a unified Docker Compose setup to run the entire backend and the Umbrel app with a single command, featuring hot-reloading for the Rust service.

### Prerequisites
- Docker and Docker Compose
- A Firebase project
- A Firebase service account key file named `admin-sdk-cred.json` placed in the `backend/` directory
- .env's: wherever you see an .env.example, replace it with your keys

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
    It's important to note that the Umbrel server will need to compile first, so it won't be ready to service requests immediately.

### Services
- **Backend API:** Accessible at `http://localhost:6969`
- **Umbrel Service:** Accessible at `http://localhost:3029`
- **Hot-Reloading:** The Umbrel (Rust) service will automatically restart when you save changes to any file in `umbrel/src/`.

### Frontend

For frontend development, use Vite's development server:
```bash
cd frontend
bun run dev
```

The frontend will be available at `http://localhost:5173` and will connect to the backend services running in Docker.

**Note:** Firebase emulators are configured for production builds and testing Firebase functions/auth. Use `firebase emulators:start` only when you need to test Firebase services with the production build.

Now you can use the entire Titanic suit on your local machine.

## Production Deployment

### Backend (`backend/`)
The backend is designed to be deployed as a Docker container. See `backend/docker-compose.run.yml` for an example of how to run the pre-built image from `ghcr.io`.

**Important:** The backend runs as a non-root user for security. Set the correct user ID to match your host user for volume permissions:
```bash
export UID=$(id -u)
export GID=$(id -g)
docker compose -f backend/docker-compose.run.yml up -d
```

Don't forget the admin SDK credentials file!

### Umbrel Component (`titanic/`)
The Umbrel component is deployed as a native Umbrel app:
   ```bash
   # On your Umbrel device
   cd /home/umbrel/umbrel/app-stores/getumbrel-umbrel-apps-github-53f74447
   
   git clone https://github.com/onkr0d/Titanic.git titanic
   cd titanic
   
   # important to have the image before installation
   sudo docker pull ghcr.io/onkr0d/titanic/titanic:latest@sha256:xyz
   # UmbrelOS needs specifically tagged images
   
   umbreld client apps.install.mutate --appId titanic
   ```
   Then configure the Firebase Project ID through the Umbrel dashboard.

## File Flow

1. User uploads video through web interface.
2. Backend receives the file and enqueues a compression job.
3. FFmpeg compresses the video using the H.265 codec (if not already H.265).
4. The backend uploads the compressed video to the Umbrel component.
5. The Umbrel component saves the video to the Plex media directory.
6. Plex automatically detects and indexes the new video.

## Security

- **Most endpoints require Firebase JWT authentication** (with development mode bypass for local testing - ensure `IS_DEV=false` in production).
    - ``/health`` endpoints are not protected
- File uploads are validated for type and size.
- Filenames are sanitized to prevent path traversal.
- Both the Rust Umbrel service and Flask backend run as non-root users.
