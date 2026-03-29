import os
import sys
import logging
import sentry_sdk
from sentry_sdk.integrations.rq import RqIntegration
from redis import Redis
from rq import Worker, Queue

logging.basicConfig(
    level=logging.DEBUG if os.environ.get('IS_DEV', 'false').lower() == 'true' else logging.INFO,
    format='%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(message)s'
)

dsn = os.environ.get("SENTRY_RQ_DSN", "").strip()
if dsn:
    sample_rate = os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "").strip()
    try:
        traces_sample_rate = float(sample_rate) if sample_rate else 1.0
    except ValueError:
        logging.warning(
            "Invalid SENTRY_TRACES_SAMPLE_RATE value %r; falling back to 1.0",
            sample_rate,
        )
        traces_sample_rate = 1.0
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT"),
        integrations=[RqIntegration()],
        send_default_pii=True,
        traces_sample_rate=traces_sample_rate,
    )

if __name__ == "__main__":
    queues = sys.argv[1:] or ["default"]
    conn = Redis()
    worker = Worker([Queue(q, connection=conn) for q in queues], connection=conn)
    worker.work()
