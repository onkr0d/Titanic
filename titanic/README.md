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
   # Pull down your image:
   sudo docker pull [tagged image]

   # Install the app using Umbrel's CLI
   umbreld client apps.install.mutate --appId titanic
   ```
## Features

- **Firebase Authentication**: Verifies JWT tokens from your Firebase project
- **Video Upload**: Receives compressed videos and saves them to your Plex media directory
- **Health Checks**: Docker health checks for monitoring