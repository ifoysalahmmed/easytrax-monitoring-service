import time
import redis

from os import getenv

from dotenv import load_dotenv
from prometheus_client import start_http_server, Gauge

load_dotenv()

QUEUE_SIZE = Gauge("celery_queue_length", "Celery queue depth", ["queue_name"])

r = redis.Redis(
    host=getenv("REDIS_HOST", "localhost"),
    port=int(getenv("REDIS_PORT", 6379)),
    password=getenv("REDIS_PASSWORD"),
    db=int(getenv("REDIS_DB", 0)),
)

QUEUES = [
    "enterprise_alarm_report", 
    "enterprise_telemetry",
    "sms-queue",
    "enterprise_periodic",
    "enterprise-command",
]

if __name__ == "__main__":
    start_http_server(int(getenv("HTTP_PORT", 9808)))
    while True:
        for queue in QUEUES:
            length = r.llen(queue)
            QUEUE_SIZE.labels(queue_name=queue).set(length)
        time.sleep(int(os.getenv("POLL_INTERVAL", 15)))
