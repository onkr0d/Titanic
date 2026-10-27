import * as Sentry from "@sentry/react";

Sentry.init({
  dsn: import.meta.env.VITE_SENTRY_DSN,
  // Send user info such as automatic IP address collection on events
  dataCollection: { userInfo: true },
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
  environment: import.meta.env.PROD ? "production" : "development",
});