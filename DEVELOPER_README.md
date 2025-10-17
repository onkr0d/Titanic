# Titanic - Developer Guide

## Getting Started (Local Development)

This project includes a unified Docker Compose setup to run the entire backend and the Umbrel app with a single command, featuring hot-reloading for the Rust service.

### Prerequisites
- Docker and Docker Compose
- A Firebase project
- A Firebase service account key file named `admin-sdk-cred.json` placed in the `backend/` directory
- .env's: wherever you see an .env.example, replace it with your keys

### Setup

**Run the services:**
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

Since we have auth everywhere:
```bash
cd frontend
firebase emulators:start
```

The frontend will be available at `http://localhost:5173` and will connect to the backend services running in Docker.

Now you can use the entire Titanic suit on your local machine.

## Production Deployment

### Backend (`backend/`)
The backend is designed to be deployed as a Docker container. See `backend/docker-compose.run.yml` for an example of how to run the pre-built image from `ghcr.io`.

```bash
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