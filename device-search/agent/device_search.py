#!/usr/bin/env python3
"""Read-only search of the parser JSON logs for one device (IMEI).

Runs on the parser server. The device-search web service calls it over SSH
with a key that is only allowed to run this script, so the command arrives in
SSH_ORIGINAL_COMMAND (or in argv when run by hand):

  list                                   parsers and how far back their logs go
  search <parser|all> <imei> <since>     records for one IMEI since <since>
                                         (UTC, YYYY-MM-DDTHH:MM:SS)

Prints one JSON document. Only reads files in LOG_DIR; changes nothing.
"""

import calendar
import gzip
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

LOG_DIR = os.environ.get("LOG_DIR", "/srv/easytrax-tcp-parser/logs")
MAX_RECORDS = 1500  # newest records returned; counts cover everything
MAX_RAW = 2000  # characters of each raw line returned

FILE_RE = re.compile(r"^([a-z0-9_]+)\.log\.(\d{4}-\d{2}-\d{2})(?:\.(\d+))?(\.gz)?$")
TS_RE = re.compile(rb'"timestamp":"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})[^"]*"\}\s*$')

# GT06-family packet numbers (concox, qs, s06a, et24, vl512, lg300)
PACKETS = {
    "01": "login",
    "12": "location",
    "22": "location",
    "a0": "location",
    "13": "heartbeat",
    "23": "heartbeat",
    "16": "alarm",
    "26": "alarm",
    "27": "alarm",
    "19": "alarm",
    "17": "lbs",
    "18": "lbs",
    "28": "lbs",
    "2c": "wifi",
    "15": "command_reply",
    "21": "command_reply",
    "8a": "time_request",
    "94": "info",
}


def fail(message):
    print(json.dumps({"error": message}))
    sys.exit(1)


def log_files():
    """{parser: [paths oldest first]} for the files in LOG_DIR."""
    found = {}
    for name in os.listdir(LOG_DIR):
        m = FILE_RE.match(name)
        if not m:
            continue
        path = os.path.join(LOG_DIR, name)
        try:
            if os.path.getsize(path) == 0:
                continue
        except OSError:
            continue
        key = (m.group(2), int(m.group(3) or 0))
        found.setdefault(m.group(1), []).append((key, path))
    return {p: [path for _, path in sorted(files)] for p, files in found.items()}


def first_timestamp(paths):
    """Receive time of the first record in the oldest readable file."""
    for path in paths:
        try:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rb") as fh:
                for _ in range(50):
                    line = fh.readline()
                    if not line:
                        break
                    m = TS_RE.search(line)
                    if m:
                        return m.group(1).decode()
        except (OSError, EOFError):
            continue  # rotated or archived while we looked
    return None


def coverage(files):
    return {p: first_timestamp(paths) for p, paths in files.items()}


def grep(path, needle):
    """Lines of path containing needle (grep is much faster than Python here)."""
    tool = "zgrep" if path.endswith(".gz") else "grep"
    try:
        out = subprocess.run(
            [tool, "-a", "-h", "-F", "--", needle, path],
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return []
    return out.stdout.splitlines()


def packet_number(hexstr):
    if hexstr.startswith("7878"):
        return hexstr[6:8]
    if hexstr.startswith("7979"):
        return hexstr[8:10]
    return ""


def kind_of(msg, pkt, payload):
    if "unhandled" in msg.lower():
        return "info" if pkt == "94" else "unhandled"  # QS ignores SIM info; harmless
    if "course status" in msg.lower():
        return "detail"  # debug copy of a location record
    if pkt == "01":
        return "login"
    if "heartbeat" in msg.lower() or payload.get("heartbeat"):
        return "heartbeat"
    if "gps" in msg.lower():
        return "location"
    return PACKETS.get(pkt, "location" if "latitude" in payload else "other")


def sim_from_hex(hexstr, imei16):
    """IMSI and ICCID from a 0x94 info packet (sub-type 0x0a)."""
    k = hexstr.find("940a" + imei16)
    if k < 0:
        return None, None
    imsi = hexstr[k + 20 : k + 36].rstrip("f")
    iccid = hexstr[k + 36 : k + 56].rstrip("f")
    # 15-digit IMSI in 8 bytes: concox pads a leading 0, QS a trailing 0
    if len(imsi) == 16:
        imsi = imsi[1:] if imsi.startswith("0") else imsi[:15]
    return imsi or None, iccid or None


def to_record(line, parser, imei15, imei16, since):
    m = TS_RE.search(line)
    if not m:
        return None
    t = m.group(1).decode()
    if t < since:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
    hexstr = (d.get("hex") or d.get("HEX") or "").lower()
    found = str(d.get("imei") or payload.get("imei") or "")
    if found:
        if found.lstrip("0") != imei15.lstrip("0"):
            return None
    elif imei16 not in hexstr:
        return None  # IMEI digits matched by chance inside other data
    msg = str(d.get("message", "")).strip(" :")
    pkt = packet_number(hexstr)
    if not pkt and "event_type" in d:
        pkt = str(d["event_type"]).lower()
    imsi, iccid = sim_from_hex(hexstr, imei16) if pkt == "94" else (None, None)
    rec = {
        "t": t,
        "parser": parser,
        "msg": msg,
        "pkt": pkt,
        "kind": kind_of(msg, pkt, payload),
        "skipped": "not send" in msg.lower(),
        "imsi": imsi,
        "iccid": iccid,
        "raw": line.decode("utf-8", "replace")[:MAX_RAW],
    }
    for key, name in (
        ("latitude", "lat"),
        ("longitude", "lon"),
        ("speed", "speed"),
        ("num_gps_sat", "sats"),
        ("gps_positioned", "fix"),
        ("engine", "engine"),
        ("vehicle_current_status", "state"),
        ("gsm_strength", "gsm"),
        ("battery_level", "battery"),
        ("volt", "volt"),
        ("charging_state", "charging"),
        ("external_power", "external_power"),
        ("vibration", "vibration"),
        ("device_type", "device_type"),
        ("timestamp", "device_time"),
    ):
        if key in payload:
            rec[name] = payload[key]
    return rec


def search(parser, imei, since):
    imei15 = imei[1:] if len(imei) == 16 and imei.startswith("0") else imei
    imei16 = imei15.rjust(16, "0")
    files = log_files()
    if parser != "all":
        if parser not in files:
            fail("unknown parser")
        files = {parser: files[parser]}
    since_epoch = calendar.timegm(time.strptime(since, "%Y-%m-%dT%H:%M:%S"))
    jobs = []
    for p, paths in files.items():
        for path in paths:
            try:
                if os.path.getmtime(path) < since_epoch:
                    continue  # finished before the window started
            except OSError:
                continue
            jobs.append((p, path))

    started = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda job: (job[0], grep(job[1], imei15)), jobs))

    records = []
    for p, lines in results:
        for line in lines:
            rec = to_record(line, p, imei15, imei16, since)
            if rec:
                records.append(rec)
    records.sort(key=lambda r: r["t"])

    counts, skipped, last, per_parser = {}, {}, {}, {}
    for r in records:
        bucket = skipped if r["skipped"] else counts
        bucket[r["kind"]] = bucket.get(r["kind"], 0) + 1
        last[r["kind"]] = r
        pp = per_parser.setdefault(
            r["parser"], {"count": 0, "first": r["t"], "last": r["t"]}
        )
        pp["count"] += 1
        pp["last"] = r["t"]
    last_sim = next((r for r in reversed(records) if r.get("imsi")), None)
    last_fix = next(
        (
            r
            for r in reversed(records)
            if r["kind"] == "location" and r.get("fix") is not False and r.get("lat")
        ),
        None,
    )

    return {
        "imei": imei15,
        "since": since,
        "now": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "files_searched": len(jobs),
        "seconds": round(time.time() - started, 2),
        "coverage": coverage(files),
        "total": len(records),
        "counts": counts,
        "skipped": skipped,
        "per_parser": per_parser,
        "last": last,
        "last_sim": last_sim,
        "last_fix": last_fix,
        "truncated": len(records) > MAX_RECORDS,
        "records": records[-MAX_RECORDS:],
    }


def main():
    command = os.environ.get("SSH_ORIGINAL_COMMAND")
    args = command.split() if command is not None else sys.argv[1:]
    if args == ["list"]:
        print(json.dumps({"coverage": coverage(log_files())}))
        return
    if len(args) == 4 and args[0] == "search":
        _, parser, imei, since = args
        if not re.fullmatch(r"all|[a-z0-9_]{1,32}", parser):
            fail("bad parser")
        if not re.fullmatch(r"\d{15,16}", imei):
            fail("bad imei")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", since):
            fail("bad since")
        print(json.dumps(search(parser, imei, since), separators=(",", ":")))
        return
    fail("usage: list | search <parser|all> <imei> <since>")


if __name__ == "__main__":
    main()
