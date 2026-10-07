"""Device search web service: one page to check a device during onboarding.

Calls device_search.py on the parser server over SSH and explains the result
in plain language (see explain.py). Standard library only.

Settings (environment):
  PARSER_SSH    user@host of the parser server
  SSH_KEY       private key that may only run device_search.py there
  KNOWN_HOSTS   known_hosts file with the parser server's host key
  LOCAL_AGENT   path to device_search.py: run it locally instead of over SSH
                (for testing; LOG_DIR is passed through)
  BACKEND_URL   Easytrax API that checks the admin's token
  ADMIN_URL     admin frontend; the only page allowed to hand over the token
  ALLOWED_ROLES user roles allowed in (default 1,6 = SystemAdmin, Admin)
  PORT          listen port (default 8080)

Every /api/ call needs "Authorization: jwt <token>" of a logged-in admin. The
token is checked with the backend's user_details endpoint and the answer is
cached for a minute.
"""

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from explain import LOCAL, explain, parser_name

HERE = Path(__file__).parent
ADMIN_URL = os.environ.get("ADMIN_URL", "https://easy-admin.etrax.xyz").rstrip("/")
BACKEND_URL = os.environ.get("BACKEND_URL", "https://platform-admin.easytrax.com.bd").rstrip("/")
ALLOWED_ROLES = {int(r) for r in os.environ.get("ALLOWED_ROLES", "1,6").split(",")}
PAGE = (HERE / "static" / "index.html").read_text(encoding="utf-8")
PAGE = PAGE.replace("__ADMIN_URL__", ADMIN_URL).encode()
SEARCHES = threading.BoundedSemaphore(3)  # parallel searches allowed
LIST_CACHE = {"at": 0, "data": None}
TOKEN_CACHE = {}  # sha256(token) -> (valid until, allowed)
TOKEN_RE = re.compile(r"jwt ([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)")


class NotAllowed(Exception):
    pass


def check_token(header):
    """Raises NotAllowed unless the header holds a logged-in admin's token."""
    m = TOKEN_RE.fullmatch(header or "")
    if not m:
        raise NotAllowed()
    key = hashlib.sha256(m.group(1).encode()).hexdigest()
    now = time.time()
    cached = TOKEN_CACHE.get(key)
    if cached and cached[0] > now:
        if not cached[1]:
            raise NotAllowed()
        return
    # Cloudflare in front of the backend blocks the default Python User-Agent.
    req = Request(f"{BACKEND_URL}/user/api/user_details/",
                  headers={"Authorization": header, "Accept": "application/json",
                           "User-Agent": "easytrax-device-check/1.0"})
    try:
        with urlopen(req, timeout=10) as resp:
            role = json.load(resp).get("role_id")
        allowed = role in ALLOWED_ROLES
        if not allowed:
            print(f"login refused: role {role} not allowed", flush=True)
    except HTTPError as e:
        is_backend = "json" in (e.headers.get("Content-Type") or "")
        print(f"login refused: backend answered {e.code}"
              f"{'' if is_backend else ' (not the backend, e.g. a proxy block page)'}", flush=True)
        if e.code >= 500 or not is_backend:
            raise RuntimeError("লগইন যাচাই করা যায়নি") from None
        allowed = False
    except (URLError, TimeoutError, ValueError) as e:
        print(f"login check failed: {e}", flush=True)
        raise RuntimeError("লগইন যাচাই করা যায়নি") from None
    if len(TOKEN_CACHE) > 1000:
        for k in [k for k, v in TOKEN_CACHE.items() if v[0] <= now]:
            del TOKEN_CACHE[k]
    TOKEN_CACHE[key] = (now + 60, allowed)
    if not allowed:
        raise NotAllowed()


def run_agent(*args):
    if os.environ.get("LOCAL_AGENT"):
        cmd = ["python3", os.environ["LOCAL_AGENT"], *args]
    else:
        cmd = [
            "ssh",
            "-i",
            os.environ["SSH_KEY"],
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={os.environ['KNOWN_HOSTS']}",
            os.environ["PARSER_SSH"],
            *args,
        ]
    out = subprocess.run(cmd, capture_output=True, timeout=90)
    try:
        data = json.loads(out.stdout)
    except ValueError:
        raise RuntimeError("পার্সার সার্ভারে পৌঁছানো যায়নি") from None
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def window_start(window):
    now = datetime.now(timezone.utc)
    if window == "today":
        start = now.astimezone(LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(minutes=int(window))
    return start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parsers():
    if time.time() - LIST_CACHE["at"] > 60:
        LIST_CACHE["data"] = run_agent("list")["coverage"]
        LIST_CACHE["at"] = time.time()
    now = datetime.now(timezone.utc)
    result = []
    for p, first in sorted(LIST_CACHE["data"].items()):
        if not first:
            continue
        t = datetime.strptime(first, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        if now - t > timedelta(days=2):
            continue  # old leftover file, parser not in use
        result.append(
            {
                "id": p,
                "name": parser_name(p),
                "from": t.astimezone(LOCAL).strftime("%H:%M"),
            }
        )
    return result


def search(query):
    imei = re.sub(r"\D", "", query.get("imei", [""])[0])
    parser = query.get("parser", ["all"])[0]
    window = query.get("window", ["60"])[0]
    if not re.fullmatch(r"\d{15,16}", imei):
        raise ValueError("ডিভাইসে লেখা 15 সংখ্যার IMEI দিন।")
    if window not in ("15", "60", "today"):
        raise ValueError("অজানা সময়।")
    if parser != "all" and not re.fullmatch(r"[a-z0-9_]{1,32}", parser):
        raise ValueError("অজানা পার্সার।")
    if not SEARCHES.acquire(timeout=30):
        raise RuntimeError("একসাথে অনেক খোঁজ চলছে, একটু পরে আবার চেষ্টা করুন।")
    try:
        result = run_agent("search", parser, imei, window_start(window))
    finally:
        SEARCHES.release()
    return explain(result, window, parser)


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, content_type="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/":
                self.send(200, PAGE, "text/html; charset=utf-8")
            elif url.path == "/health":
                self.send(200, {"ok": True})
            elif url.path in ("/api/parsers", "/api/search"):
                check_token(self.headers.get("Authorization"))
                if url.path == "/api/parsers":
                    self.send(200, {"parsers": parsers()})
                else:
                    self.send(200, search(parse_qs(url.query)))
            else:
                self.send(404, {"error": "not found"})
        except NotAllowed:
            self.send(401, {"error": "login"})
        except ValueError as e:
            self.send(400, {"error": str(e)})
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            self.send(502, {"error": f"খোঁজা যায়নি: {e}"})

    def log_message(self, fmt, *args):
        # Leave IMEIs out of the container log.
        print(
            f"{self.address_string()} {self.command} {urlparse(self.path).path} "
            f"{args[1] if len(args) > 1 else ''}",
            flush=True,
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"device-search listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
