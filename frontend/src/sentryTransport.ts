/**
 * Creates a custom Sentry transport function that proxies requests through our backend
 * to avoid adblocker detection
 */
export function createProxyTransport(dsn: string, proxyUrl: string) {
  return () => ({
    send: async (envelope: any): Promise<any> => {
      try {
        // Serialize the envelope to the format Sentry expects
        const body = serializeEnvelope(envelope);
        
        const response = await fetch(proxyUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-sentry-envelope',
            'X-Sentry-DSN': dsn,
          },
          body,
        });

        if (!response.ok) {
          return {
            status: 'failed',
            reason: `HTTP ${response.status}`,
          };
        }

        return {
          status: 'success',
        };
      } catch (error) {
        console.warn('Failed to send Sentry event through proxy:', error);
        return {
          status: 'failed',
          reason: error instanceof Error ? error.message : 'Unknown error',
        };
      }
    },
    flush: async (_timeout?: number): Promise<boolean> => {
      // Simple flush implementation - in a real implementation, 
      // you might want to wait for pending requests to complete
      return Promise.resolve(true);
    }
  });
}

function serializeEnvelope(envelope: any): string {
  const [headers, items] = envelope;
  
  let body = JSON.stringify(headers) + '\n';
  
  for (const item of items) {
    const [itemHeaders, payload] = item;
    body += JSON.stringify(itemHeaders) + '\n';
    
    if (typeof payload === 'string') {
      body += payload;
    } else {
      body += JSON.stringify(payload);
    }
    body += '\n';
  }
  
  return body;
}