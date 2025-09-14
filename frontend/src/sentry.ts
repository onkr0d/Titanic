import * as Sentry from "@sentry/react";
import { createProxyTransport } from "./sentryTransport";

// Determine the API base URL (same logic as in api.ts)
const API_BASE_URL = (import.meta.env.DEV ? 'http://localhost:6969' : 'https://compress.ivan.boston') + '/api';

Sentry.init({
  dsn: import.meta.env.VITE_SENTRY_DSN,
  // Setting this option to true will send default PII data to Sentry.
  // For example, automatic IP address collection on events
  sendDefaultPii: true,
  integrations: [Sentry.browserTracingIntegration()],

  // Set tracesSampleRate to 1.0 to capture 100%
  // of transactions for performance monitoring.
  // We recommend adjusting this value in production
  tracesSampleRate: 1.0,
  // Set `tracePropagationTargets` to control for which URLs distributed tracing should be enabled
  tracePropagationTargets: [
    /^\//,                               // same-origin (relative) calls
    /^https:\/\/compress\.ivan\.boston/, // prod API
    /^https?:\/\/localhost:6969/,        // dev API  
  ],
  enabled: import.meta.env.PROD,
  
  // Use custom transport to proxy through backend
  transport: import.meta.env.VITE_SENTRY_DSN ? 
    createProxyTransport(import.meta.env.VITE_SENTRY_DSN, `${API_BASE_URL}/sentry-proxy`) : 
    undefined

});