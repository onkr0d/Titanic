# Sentry Adblocker Bypass Implementation

This implementation solves the issue where adblockers block direct requests to Sentry servers by proxying all Sentry requests through the backend.

## How it works

### Problem
- Adblockers detect and block requests to `*.ingest.sentry.io` domains
- This prevents error reporting and performance monitoring from working for users with adblockers

### Solution
- Route all Sentry requests through the backend API instead of directly to Sentry servers
- Backend acts as a proxy, forwarding requests to the actual Sentry API
- To adblockers, this appears as regular API traffic to the application's own domain

## Implementation Details

### Backend Changes (`backend/app.py`)

Added `/api/sentry-proxy` endpoint that:
1. Accepts Sentry envelope data from frontend
2. Extracts DSN from `X-Sentry-DSN` header
3. Parses DSN to extract authentication key and endpoint URL
4. Forwards request to actual Sentry API with proper authentication
5. Returns response back to frontend

```python
@app.route('/api/sentry-proxy', methods=['POST'])
def sentry_proxy():
    # Extract DSN and parse authentication details
    # Forward to actual Sentry API
    # Return response
```

### Frontend Changes

1. **Custom Transport (`frontend/src/sentryTransport.ts`)**
   - Implements Sentry's Transport interface
   - Serializes Sentry envelopes to the correct format
   - Sends requests to backend proxy instead of Sentry directly

2. **Sentry Configuration (`frontend/src/sentry.ts`)**
   - Updated to use custom transport when DSN is available
   - Maintains all existing Sentry features (tracing, performance monitoring, etc.)

3. **Build Configuration (`frontend/vite.config.ts`)**
   - Made Sentry Vite plugin conditional to avoid build issues when Sentry CLI is not available

## Testing

The implementation has been tested with:
- ✅ Backend proxy correctly parses DSN and forwards requests
- ✅ Frontend transport successfully sends data through proxy
- ✅ No CORS issues (backend already configured for cross-origin requests)
- ✅ Build process works without Sentry CLI dependencies

## Deployment Notes

- No additional environment variables required
- Existing `VITE_SENTRY_DSN` environment variable is used
- Backend automatically enables proxy when Sentry DSN is provided in requests
- Solution is backwards compatible and doesn't affect existing Sentry functionality

## Benefits

1. **Adblocker Bypass**: Sentry requests appear as regular API calls to the application's domain
2. **Zero Configuration**: Works automatically when Sentry DSN is configured
3. **Full Compatibility**: Maintains all existing Sentry features and functionality
4. **Performance**: Minimal overhead, requests are simply proxied through backend
5. **Security**: DSN authentication is handled securely by the backend