# Device check

A web page for device onboarding: enter an IMEI and see, in plain language, whether the device is reaching the parser server and what it sent today.

```
browser --> nginx (password) --> device-search service (monitoring host)
        --> SSH, key limited to one command --> device_search.py (parser server)
        --> parser JSON logs of today (read-only)
```

The page searches the parser JSON logs that the parser server keeps until the nightly 04:00 UTC archive run. Each parser's "data from" time on the page is the first record still on the server; older data has moved to the storage server.

## What it shows

- **Status** with what to check next:
  - No data from this device
  - Device has gone quiet
  - Messages the parser does not understand
  - Connected, waiting for the first location
  - Only status check-ins
  - No GPS signal
  - Online and sending data
- **Details**: parser, last heard from, last login, GPS signal, last location (map link), engine, mobile signal and power, SIM operator (IMSI/ICCID from Concox and QS info packets), message counts.
- **Timeline**, newest first, one plain sentence per message. Developers can turn on the raw log lines.

"Not forwarded" messages are normal parser filtering:
- **Most parsers** drop repeats that arrive within 2 seconds (5 seconds for check-ins).
- **QS** forwards one location every 5 minutes while the engine is off.

Parsers that do not write JSON logs (G65, GK309E, e1a, gp33, demo_all) cannot be searched.

## Files

| File | Runs on | Purpose |
|---|---|---|
| `agent/device_search.py` | parser server | Read-only search; Python 3 standard library only |
| `app/server.py` | monitoring host | Web service, calls the agent over SSH |
| `app/explain.py` | monitoring host | Turns results into plain-language status and timeline |
| `app/static/index.html` | browser | The page |
| `docker-compose.yml`, `Dockerfile` | monitoring host | Runs the web service on 127.0.0.1 |

## Setup

### 1. Parser server

Copy `agent/device_search.py` to a folder owned by the parser user, for example `~/device-search/`. Then add the monitoring host's public search key to that user's `~/.ssh/authorized_keys`, limited to this one script and to the monitoring host's address:

```
command="/usr/bin/python3 /home/ubuntu/device-search/device_search.py",restrict,from="<monitoring host IP>" ssh-ed25519 AAAA... device-search
```

With `command=` the key can only run the script; the requested command arrives in `SSH_ORIGINAL_COMMAND` and the script accepts only `list` and `search <parser|all> <imei> <since>`.

### 2. Monitoring host

1. Create a key pair and a `known_hosts` entry for the parser server, in a root-only folder outside the repo.
2. Copy `sample.env` to `.env` and fill it in.
3. Start the service:

   ```bash
   cd device-search
   docker compose up -d --build
   ```

4. Put nginx with a basic-auth password in front of `127.0.0.1:9811`.

After a `git pull` that changes this folder, run `docker compose up -d --build` again here. If `agent/device_search.py` changed, copy it to the parser server again.

## Test from a terminal

On the parser server:

```bash
python3 ~/device-search/device_search.py list
python3 ~/device-search/device_search.py search all 865784054861086 2026-10-07T00:00:00
```
