"""Turns a device_search.py result into plain language for the onboarding page."""

from datetime import datetime, timedelta, timezone

LOCAL = timezone(timedelta(hours=6))  # Bangladesh time
LIVE_MINUTES = 10  # heard from within this = online now

PARSER_NAMES = {
    "concox": "Concox",
    "qs": "QS",
    "s06a": "S06A",
    "teltonika": "Teltonika",
    "vl512": "VL512",
    "lg300": "LG300",
    "et24": "ET24",
    "esino": "Esino",
    "topten": "TopTen",
}

OPERATORS = {
    "47001": "Grameenphone",
    "47002": "Robi",
    "47003": "Banglalink",
    "47004": "Teletalk",
    "47007": "Airtel",
}

STATES = {
    "driving": "driving",
    "idle": "engine on, standing still",
    "engine_off": "engine off",
}

WINDOWS = {"15": "in the last 15 minutes", "60": "in the last hour", "today": "today"}


def parser_name(p):
    return PARSER_NAMES.get(p, p.upper())


def utc(ts):
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def local_time(ts, now):
    t = utc(ts).astimezone(LOCAL)
    if t.date() != now.astimezone(LOCAL).date():
        return t.strftime("%d %b %H:%M:%S")
    return t.strftime("%H:%M:%S")


def ago(ts, now):
    minutes = int((now - utc(ts)).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min ago"


def map_link(r):
    lat, lon = r.get("lat"), r.get("lon")
    if not lat and not lon:
        return None
    return f"https://www.google.com/maps?q={lat},{lon}"


def has_fix(r):
    if "fix" in r:
        return bool(r["fix"])
    return bool(r.get("lat") or r.get("lon")) and r.get("sats", 1) != 0


def operator(imsi):
    return OPERATORS.get((imsi or "")[:5])


def condition(r):
    """Engine / movement / power, as a short phrase."""
    parts = []
    if r.get("state") in STATES:
        parts.append(STATES[r["state"]])
    elif "engine" in r:
        parts.append("engine on" if r["engine"] else "engine off")
    if r.get("speed"):
        parts.append(f"{r['speed']} km/h")
    return ", ".join(parts)


def power(r):
    parts = []
    if r.get("gsm") is not None:
        parts.append(f"mobile signal {r['gsm']}%")
    if r.get("battery") is not None:
        parts.append(f"battery {r['battery']}%")
    if r.get("volt"):
        parts.append(f"vehicle power {r['volt']} V")
    if r.get("charging") is True:
        parts.append("charging")
    return ", ".join(parts)


def sentence(r):
    """One timeline line for a record."""
    kind, skipped = r["kind"], r["skipped"]
    if kind == "login":
        return "Device connected and logged in", "good"
    if kind == "location":
        if skipped and r["parser"] == "qs":
            return ("Location not forwarded: engine off, the QS parser forwards "
                    "one location every 5 minutes while parked (normal)", "muted")
        if skipped:
            return "Location skipped: repeated within 2 seconds (normal)", "muted"
        text = "Location received"
        cond = condition(r)
        if cond:
            text += f": {cond}"
        if not has_fix(r):
            return text + " - no GPS signal, last known position", "warn"
        return text, "good"
    if kind == "heartbeat":
        if skipped:
            return (
                "Status check-in skipped: repeated within 5 seconds (normal)",
                "muted",
            )
        details = ", ".join(p for p in (condition(r), power(r)) if p)
        return "Status check-in" + (f": {details}" if details else ""), "good"
    if kind == "alarm":
        extra = []
        if r.get("vibration"):
            extra.append("vibration")
        if r.get("external_power") is True:
            extra.append("external power connected")
        details = ", ".join(extra + [p for p in (condition(r),) if p])
        return "Alarm / status message" + (f": {details}" if details else ""), "info"
    if kind == "info":
        if r.get("imsi"):
            op = operator(r["imsi"])
            return f"Device reported its SIM card ({op or 'unknown operator'})", "info"
        if r.get("volt"):
            return f"Device information: vehicle power {r['volt']} V", "info"
        return "Device information message", "info"
    if kind == "unhandled":
        return (
            f"Message the parser does not understand (type 0x{r['pkt']}) - ignored",
            "bad",
        )
    if kind == "time_request":
        return "Device asked the server for the time", "muted"
    if kind == "command_reply":
        return "Device replied to a command", "info"
    if kind in ("lbs", "wifi"):
        return "Rough location from mobile towers / WiFi (no GPS)", "warn"
    return r.get("msg") or "Other message", "muted"


def status(result, window, parser, now):
    counts = result["counts"]
    last = result["last"]
    when = WINDOWS[window]
    locations = counts.get("location", 0)
    heartbeats = counts.get("heartbeat", 0)

    if result["total"] == 0:
        hints = [
            "Is the device powered on? Check the power wire and the device LEDs.",
            "Does the SIM card have mobile data (internet) balance?",
            "Is the server IP address and port set correctly on the device (by SMS command)?",
            "Is the APN set for the SIM operator?",
        ]
        if parser != "all":
            hints.append('Is this the right parser? Try searching with "Any parser".')
        if window != "today":
            hints.append('Try a longer time: "All of today".')
        return (
            "bad",
            "No data from this device",
            (f"The parser has not received anything from this device {when}."),
            hints,
        )

    newest = max(r["t"] for r in last.values())
    silent = now - utc(newest) > timedelta(minutes=LIVE_MINUTES)
    if silent:
        return (
            "warn",
            "Device has gone quiet",
            (
                f"The device was sending data, but nothing has arrived since "
                f"{local_time(newest, now)} ({ago(newest, now)})."
            ),
            [
                "The device may be switched off, or the vehicle may be somewhere without mobile network.",
                "Check the SIM card's data balance.",
                "If the power wire was disconnected, reconnect it and search again.",
            ],
        )

    if counts.get("unhandled") and not locations:
        return (
            "bad",
            "Device sends messages the parser does not understand",
            (
                "The device is talking to the server, but the parser ignores its messages, "
                "so no location reaches Easytrax."
            ),
            [
                "The device model may not match this parser. Check that it is set to the right server port.",
                "Tell the developers the IMEI and the message type shown below.",
            ],
        )

    if not locations and not heartbeats:
        return (
            "warn",
            "Connected, waiting for the first location",
            ("The device has connected to the server but has not sent a location yet."),
            [
                "Wait a few minutes.",
                "Check the device's upload interval setting.",
            ],
        )

    if not locations:
        return (
            "warn",
            "Connected, but sending only status check-ins",
            (
                "The device is online but has not sent any location "
                f"{when}. It only sends regular status check-ins."
            ),
            [
                "Turn the vehicle's ignition on; many devices only send locations while it is on.",
                "Check the device's upload interval setting.",
            ],
        )

    last_loc = last.get("location")
    if last_loc and not has_fix(last_loc):
        return (
            "warn",
            "Online, but no GPS signal",
            (
                "The device is sending data, but its GPS cannot find its position, "
                "so it repeats the last known position."
            ),
            [
                "Move the vehicle or device outdoors, away from buildings and roofs.",
                "Check that the GPS antenna is connected and not covered by metal.",
            ],
        )

    return (
        "good",
        "Device is online and sending data",
        (f"Locations are reaching the server. Last message {ago(newest, now)}."),
        [
            "If the device still shows offline in the Easytrax app, check that this IMEI is "
            "added to the right account and vehicle in Easytrax.",
        ],
    )


def explain(result, window, parser):
    now = utc(result["now"])
    level, title, summary, hints = status(result, window, parser, now)
    counts, skipped, last = result["counts"], result["skipped"], result["last"]

    facts = []
    if result["per_parser"]:
        facts.append(
            (
                "Found on parser",
                ", ".join(
                    f"{parser_name(p)} ({v['count']} messages)"
                    for p, v in result["per_parser"].items()
                ),
            )
        )
    if last:
        newest = max(r["t"] for r in last.values())
        facts.append(
            ("Last heard from", f"{local_time(newest, now)} ({ago(newest, now)})")
        )
        login = last.get("login")
        facts.append(
            (
                "Last login",
                (
                    local_time(login["t"], now)
                    if login
                    else "not in this period (normal if it connected earlier)"
                ),
            )
        )
        loc = last.get("location")
        if loc:
            facts.append(
                (
                    "GPS signal",
                    "yes" if has_fix(loc) else "no - position is the last known one",
                )
            )
            fix = result.get("last_fix") or loc
            facts.append(
                (
                    "Last location",
                    {
                        "text": f"{fix.get('lat'):.5f}, {fix.get('lon'):.5f} at {local_time(fix['t'], now)}",
                        "link": map_link(fix),
                    },
                )
            )
        latest = max(last.values(), key=lambda r: r["t"])
        if condition(latest):
            facts.append(("Vehicle", condition(latest)))
        with_power = [r for r in last.values() if power(r)]
        if with_power:
            facts.append(
                ("Signal and power", power(max(with_power, key=lambda r: r["t"])))
            )
        sim = result.get("last_sim")
        if sim:
            facts.append(
                (
                    "SIM card",
                    f"{operator(sim['imsi']) or 'unknown operator'} "
                    f"(IMSI {sim['imsi']}, ICCID {sim['iccid']})",
                )
            )
        facts.append(
            (
                "Messages",
                ", ".join(
                    filter(
                        None,
                        [
                            f"{counts.get('location', 0)} locations",
                            (
                                f"{counts['heartbeat']} status check-ins"
                                if counts.get("heartbeat")
                                else ""
                            ),
                            f"{counts['alarm']} alarms" if counts.get("alarm") else "",
                            (
                                f"{counts['unhandled']} not understood"
                                if counts.get("unhandled")
                                else ""
                            ),
                            (
                                f"{sum(skipped.values())} not forwarded (normal filtering)"
                                if skipped
                                else ""
                            ),
                        ],
                    )
                ),
            )
        )

    timeline = []
    for r in reversed(result["records"]):
        if r["kind"] == "detail":
            continue
        text, tone = sentence(r)
        timeline.append(
            {
                "time": local_time(r["t"], now),
                "parser": parser_name(r["parser"]),
                "text": text,
                "tone": tone,
                "map": (
                    map_link(r)
                    if r["kind"] == "location" and not r["skipped"]
                    else None
                ),
                "raw": r["raw"],
            }
        )

    coverage = []
    for p, first in sorted(result["coverage"].items()):
        coverage.append(
            {
                "parser": parser_name(p),
                "from": local_time(first, now) if first else None,
            }
        )

    return {
        "imei": result["imei"],
        "level": level,
        "title": title,
        "summary": summary,
        "hints": hints,
        "facts": [{"label": k, "value": v} for k, v in facts],
        "timeline": timeline,
        "truncated": result["truncated"],
        "coverage": coverage,
        "seconds": result["seconds"],
    }
