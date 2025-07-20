import { sentryVitePlugin } from "@sentry/vite-plugin";
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), sentryVitePlugin({
    org: "ivan-g0",
    project: "titanic-react",
    authToken: process.env.SENTRY_AUTH_TOKEN
  })],

  build: {
    sourcemap: true
  }
})