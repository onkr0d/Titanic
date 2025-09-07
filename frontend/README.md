# Titanic Frontend

Web interface for video upload and compression. Built with React, TypeScript, and Vite.

## Overview

The frontend provides:
- Drag & drop video upload interface
- Folder organization
- Real-time upload progress
- Google OAuth authentication
- Responsive design with dark/light mode

## Local Development

### Prerequisites
- Node.js 18+ or Bun
- Firebase project with authentication enabled
- Backend API running (see main README)
- Replace .env.example with your own keys

### Setup

3. **Run development server:**
   ```bash
   bun run dev
   ```
   Available at `http://localhost:5173`

## Production Deployment

### Firebase Hosting
```bash
bun run build
firebase deploy --only hosting
```