# Easytrax Monitoring Service

Central monitoring for the Easytrax platform: Celery queue lengths, server resource usage and logs, in one place.

```
Redis ──► celery-exporter ─┐
servers ─► node_exporter ──┼─► Prometheus ─► Grafana (dashboards) 
Postgres ► postgres_exporter┘        └─► alert rules
app logs ─► Fluent Bit ─► Elasticsearch ─► Kibana
```

## Repository layout

| Path | What it is |
|---|---|
| `docker-compose.yml` | Central stack: Prometheus and node_exporter. Run on the monitoring host. |
| `prometheus/prometheus.yml` | Scrape targets. |
| `prometheus/rules/alerts.yml` | Alert rules (target down, queue backlog, disk, memory). |
| `grafana/dashboards/` | Exported Grafana dashboards (backup and import source). |
| `celery-exporter/` | Small Python exporter that publishes `celery_queue_length{queue_name}` from Redis. Runs next to the broker. |
| `node-exporter/` | Docker Compose for node_exporter on a server that needs host metrics. |
| `fluent-bit/` | Docker Compose that tails the Celery alarm log and ships it to Elasticsearch. |

Grafana and Kibana are installed on the monitoring host as system services and are not run from this repo. Their dashboards and data source are described below.

## Deploying the central stack

On the monitoring host:

```bash
git clone https://github.com/ifoysalahmmed/easytrax-monitoring-service.git
cd easytrax-monitoring-service
docker compose up -d
```

Prometheus listens on port 9090 and node_exporter on 9100. Both use host networking. After changing `prometheus/prometheus.yml` or the rules, reload with:

```bash
docker compose restart prometheus
```

Then, in Grafana, add a Prometheus data source pointing at `http://<monitoring-host>:9090` and import `grafana/dashboards/celery-monitoring.json`.

## Adding a server to monitor

1. Run node_exporter on it (see `node-exporter/`, default port 9100).
2. Add its address under the `node` job in `prometheus/prometheus.yml`, with a `host` label.
3. Restart Prometheus.

## Celery exporter

Runs on the host that has the Redis broker.

```bash
cd celery-exporter
# create .env with the variables below
pip install -r requirements.txt
python celery_exporter.py
```

Configuration is read from `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | none | Redis password |
| `REDIS_DB` | `0` | Redis database |
| `HTTP_PORT` | `9808` | Port metrics are served on |
| `POLL_INTERVAL` | `15` | Seconds between queue-length reads |

Monitored queues: `enterprise_alarm_report`, `enterprise_telemetry`, `sms-queue`, `enterprise_periodic`, `enterprise-command`.

## Alerts

Defined in `prometheus/rules/alerts.yml`:

- `TargetDown`: a scrape target is down for 5 minutes.
- `CeleryQueueBacklog`: a queue has more than 5000 waiting tasks for 10 minutes.
- `DiskAlmostFull`: a filesystem is more than 85% full for 10 minutes.
- `HighMemoryUse`: memory use is above 90% for 10 minutes.

Alerts go through Alertmanager (`alertmanager/alertmanager.yml`) to the "Easytrax Alert" Telegram group. The bot token is not in git: on the monitoring host, put it in `/srv/monitoring/secrets/telegram_token` (readable only by the container user) before running `docker compose up -d`.

## Secrets

`.env` files are git-ignored. Never commit passwords or tokens.
