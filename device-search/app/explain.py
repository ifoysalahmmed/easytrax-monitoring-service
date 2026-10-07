"""Turns a device_search.py result into plain Bengali for the onboarding page."""

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
    "47001": "গ্রামীণফোন",
    "47002": "রবি",
    "47003": "বাংলালিংক",
    "47004": "টেলিটক",
    "47007": "এয়ারটেল",
}

STATES = {
    "driving": "চলছে",
    "idle": "ইঞ্জিন চালু, দাঁড়িয়ে আছে",
    "engine_off": "ইঞ্জিন বন্ধ",
}

WINDOWS = {"15": "গত 15 মিনিটে", "60": "গত 1 ঘণ্টায়", "today": "আজ"}


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
        return "এইমাত্র"
    if minutes < 60:
        return f"{minutes} মিনিট আগে"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ঘণ্টা {minutes} মিনিট আগে"


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
    """Engine / movement, as a short phrase."""
    parts = []
    if r.get("state") in STATES:
        parts.append(STATES[r["state"]])
    elif "engine" in r:
        parts.append("ইঞ্জিন চালু" if r["engine"] else "ইঞ্জিন বন্ধ")
    if r.get("speed"):
        parts.append(f"{r['speed']} কিমি/ঘণ্টা")
    return ", ".join(parts)


def power(r):
    parts = []
    if r.get("gsm") is not None:
        parts.append(f"মোবাইল নেটওয়ার্ক {r['gsm']}%")
    if r.get("battery") is not None:
        parts.append(f"ব্যাটারি {r['battery']}%")
    if r.get("volt"):
        parts.append(f"গাড়ির পাওয়ার {r['volt']} V")
    if r.get("charging") is True:
        parts.append("চার্জ হচ্ছে")
    return ", ".join(parts)


def sentence(r):
    """One timeline line for a record: (text, tone)."""
    kind, skipped = r["kind"], r["skipped"]
    if kind == "login":
        return "ডিভাইস সার্ভারে যুক্ত হয়েছে (লগইন)", "good"
    if kind == "location":
        if skipped and r["parser"] == "qs":
            return (
                "লোকেশন পাঠানো হয়নি: ইঞ্জিন বন্ধ থাকলে QS পার্সার "
                "প্রতি 5 মিনিটে একটি লোকেশন পাঠায় (স্বাভাবিক)",
                "muted",
            )
        if skipped:
            return (
                "লোকেশন পাঠানো হয়নি: 2 সেকেন্ডের মধ্যে একই তথ্য আবার এসেছে (স্বাভাবিক)",
                "muted",
            )
        text = "লোকেশন পাওয়া গেছে"
        cond = condition(r)
        if cond:
            text += f": {cond}"
        if not has_fix(r):
            return text + " - GPS সিগন্যাল নেই, শেষ জানা অবস্থান", "warn"
        return text, "good"
    if kind == "heartbeat":
        if skipped:
            return (
                "স্ট্যাটাস বার্তা পাঠানো হয়নি: 5 সেকেন্ডের মধ্যে আবার এসেছে (স্বাভাবিক)",
                "muted",
            )
        details = ", ".join(p for p in (condition(r), power(r)) if p)
        return "স্ট্যাটাস বার্তা (ডিভাইস সচল আছে)" + (
            f": {details}" if details else ""
        ), "good"
    if kind == "alarm":
        extra = []
        if r.get("vibration"):
            extra.append("ঝাঁকুনি")
        if r.get("external_power") is True:
            extra.append("বাইরের পাওয়ার যুক্ত")
        if condition(r):
            extra.append(condition(r))
        details = ", ".join(extra)
        return "অ্যালার্ম / স্ট্যাটাস বার্তা" + (f": {details}" if details else ""), "info"
    if kind == "info":
        if r.get("imsi"):
            op = operator(r["imsi"])
            return f"ডিভাইস সিম কার্ডের তথ্য পাঠিয়েছে ({op or 'অজানা অপারেটর'})", "info"
        if r.get("volt"):
            return f"ডিভাইসের তথ্য: গাড়ির পাওয়ার {r['volt']} V", "info"
        return "ডিভাইসের তথ্য বার্তা", "info"
    if kind == "unhandled":
        return (
            f"পার্সার এই বার্তাটি বুঝতে পারেনি (টাইপ 0x{r['pkt']}) - বাদ দেওয়া হয়েছে",
            "bad",
        )
    if kind == "time_request":
        return "ডিভাইস সার্ভারের কাছে সময় জানতে চেয়েছে", "muted"
    if kind == "command_reply":
        return "ডিভাইস একটি কমান্ডের উত্তর দিয়েছে", "info"
    if kind in ("lbs", "wifi"):
        return "মোবাইল টাওয়ার / WiFi থেকে আনুমানিক অবস্থান (GPS নয়)", "warn"
    return r.get("msg") or "অন্য বার্তা", "muted"


def status(result, window, parser, now):
    """(level, title, summary, hints) for the status box."""
    counts = result["counts"]
    last = result["last"]
    when = WINDOWS[window]
    locations = counts.get("location", 0)
    heartbeats = counts.get("heartbeat", 0)

    if result["total"] == 0:
        hints = [
            "ডিভাইসে কি পাওয়ার আছে? পাওয়ারের তার এবং ডিভাইসের লাইট (LED) দেখুন।",
            "সিম কার্ডে কি মোবাইল ডেটা (ইন্টারনেট) ব্যালেন্স আছে?",
            "ডিভাইসে সার্ভারের IP ঠিকানা ও পোর্ট (SMS কমান্ড দিয়ে) ঠিকভাবে সেট করা আছে কি?",
            "সিম অপারেটরের APN সেট করা আছে কি?",
        ]
        if parser != "all":
            hints.append("পার্সার কি ঠিক আছে? Parser থেকে \"Any parser\" বেছে আবার খুঁজুন।")
        if window != "today":
            hints.append("আরও বেশি সময় দেখুন: Time থেকে \"All of today\" বেছে নিন।")
        return (
            "bad",
            "এই ডিভাইস থেকে কোনো তথ্য আসেনি",
            f"পার্সার {when} এই ডিভাইস থেকে কিছুই পায়নি।",
            hints,
        )

    newest = max(r["t"] for r in last.values())
    if now - utc(newest) > timedelta(minutes=LIVE_MINUTES):
        return (
            "warn",
            "ডিভাইস চুপ হয়ে গেছে",
            f"ডিভাইসটি তথ্য পাঠাচ্ছিল, কিন্তু {local_time(newest, now)} "
            f"({ago(newest, now)}) এর পর আর কিছু আসেনি।",
            [
                "ডিভাইস বন্ধ হয়ে যেতে পারে, অথবা গাড়িটি এমন জায়গায় আছে যেখানে মোবাইল নেটওয়ার্ক নেই।",
                "সিম কার্ডের ডেটা ব্যালেন্স দেখুন।",
                "পাওয়ারের তার খুলে গিয়ে থাকলে আবার লাগিয়ে আবার খুঁজুন।",
            ],
        )

    if counts.get("unhandled") and not locations:
        return (
            "bad",
            "ডিভাইস এমন বার্তা পাঠাচ্ছে যা পার্সার বোঝে না",
            "ডিভাইসটি সার্ভারের সাথে যোগাযোগ করছে, কিন্তু পার্সার তার বার্তাগুলো বাদ দিচ্ছে, "
            "তাই কোনো লোকেশন Easytrax-এ পৌঁছাচ্ছে না।",
            [
                "ডিভাইসের মডেল এই পার্সারের সাথে নাও মিলতে পারে। ডিভাইসটি সঠিক সার্ভার পোর্টে সেট করা আছে কি না দেখুন।",
                "ডেভেলপারদের IMEI এবং নিচে দেখানো বার্তার টাইপ জানান।",
            ],
        )

    if not locations and not heartbeats:
        return (
            "warn",
            "যুক্ত হয়েছে, প্রথম লোকেশনের অপেক্ষায়",
            "ডিভাইসটি সার্ভারে যুক্ত হয়েছে কিন্তু এখনো কোনো লোকেশন পাঠায়নি।",
            ["কয়েক মিনিট অপেক্ষা করুন।", "ডিভাইসের আপলোড ইন্টারভাল সেটিং দেখুন।"],
        )

    if not locations:
        return (
            "warn",
            "যুক্ত আছে, কিন্তু শুধু স্ট্যাটাস বার্তা পাঠাচ্ছে",
            f"ডিভাইসটি অনলাইনে আছে কিন্তু {when} কোনো লোকেশন পাঠায়নি, "
            "শুধু নিয়মিত স্ট্যাটাস বার্তা পাঠাচ্ছে।",
            [
                "গাড়ির ইঞ্জিন (ইগনিশন) চালু করুন; অনেক ডিভাইস শুধু ইঞ্জিন চালু থাকলে লোকেশন পাঠায়।",
                "ডিভাইসের আপলোড ইন্টারভাল সেটিং দেখুন।",
            ],
        )

    last_loc = last.get("location")
    if last_loc and not has_fix(last_loc):
        return (
            "warn",
            "অনলাইনে আছে, কিন্তু GPS সিগন্যাল নেই",
            "ডিভাইসটি তথ্য পাঠাচ্ছে, কিন্তু GPS তার অবস্থান খুঁজে পাচ্ছে না, "
            "তাই শেষ জানা অবস্থানটিই বারবার পাঠাচ্ছে।",
            [
                "গাড়ি বা ডিভাইসটি খোলা জায়গায় নিন, দালান ও ছাদ থেকে দূরে।",
                "GPS অ্যান্টেনা ঠিকভাবে লাগানো আছে এবং ধাতু দিয়ে ঢাকা নেই কি না দেখুন।",
            ],
        )

    return (
        "good",
        "ডিভাইস অনলাইনে আছে এবং তথ্য পাঠাচ্ছে",
        f"লোকেশন সার্ভারে পৌঁছাচ্ছে। শেষ বার্তা এসেছে {ago(newest, now)}।",
        [
            "Easytrax অ্যাপে এখনো অফলাইন দেখালে, এই IMEI সঠিক অ্যাকাউন্ট ও গাড়িতে "
            "যোগ করা আছে কি না দেখুন।"
        ],
    )


def message_counts(counts, skipped):
    parts = [f"{counts.get('location', 0)}টি লোকেশন"]
    if counts.get("heartbeat"):
        parts.append(f"{counts['heartbeat']}টি স্ট্যাটাস বার্তা")
    if counts.get("alarm"):
        parts.append(f"{counts['alarm']}টি অ্যালার্ম")
    if counts.get("unhandled"):
        parts.append(f"{counts['unhandled']}টি বোঝা যায়নি")
    if skipped:
        parts.append(f"{sum(skipped.values())}টি পাঠানো হয়নি (স্বাভাবিক ফিল্টার)")
    return ", ".join(parts)


def explain(result, window, parser):
    now = utc(result["now"])
    level, title, summary, hints = status(result, window, parser, now)
    counts, skipped, last = result["counts"], result["skipped"], result["last"]

    facts = []
    if result["per_parser"]:
        facts.append((
            "পার্সার",
            ", ".join(f"{parser_name(p)} ({v['count']}টি বার্তা)"
                      for p, v in result["per_parser"].items()),
        ))
    if last:
        newest = max(r["t"] for r in last.values())
        facts.append(("শেষ যোগাযোগ", f"{local_time(newest, now)} ({ago(newest, now)})"))
        login = last.get("login")
        facts.append((
            "শেষ লগইন",
            local_time(login["t"], now) if login
            else "এই সময়ের মধ্যে নেই (আগে যুক্ত হয়ে থাকলে এটা স্বাভাবিক)",
        ))
        loc = last.get("location")
        if loc:
            facts.append(("GPS সিগন্যাল",
                          "আছে" if has_fix(loc) else "নেই - শেষ জানা অবস্থান দেখানো হচ্ছে"))
            fix = result.get("last_fix") or loc
            facts.append((
                "শেষ অবস্থান",
                {
                    "text": f"{fix.get('lat'):.5f}, {fix.get('lon'):.5f} "
                            f"({local_time(fix['t'], now)})",
                    "link": map_link(fix),
                },
            ))
        latest = max(last.values(), key=lambda r: r["t"])
        if condition(latest):
            facts.append(("গাড়ি", condition(latest)))
        with_power = [r for r in last.values() if power(r)]
        if with_power:
            facts.append(("নেটওয়ার্ক ও পাওয়ার",
                          power(max(with_power, key=lambda r: r["t"]))))
        sim = result.get("last_sim")
        if sim:
            facts.append((
                "সিম কার্ড",
                f"{operator(sim['imsi']) or 'অজানা অপারেটর'} "
                f"(IMSI {sim['imsi']}, ICCID {sim['iccid']})",
            ))
        facts.append(("বার্তা", message_counts(counts, skipped)))

    timeline = []
    for r in reversed(result["records"]):
        if r["kind"] == "detail":
            continue
        text, tone = sentence(r)
        timeline.append({
            "time": local_time(r["t"], now),
            "parser": parser_name(r["parser"]),
            "text": text,
            "tone": tone,
            "map": map_link(r) if r["kind"] == "location" and not r["skipped"] else None,
            "raw": r["raw"],
        })

    coverage = [
        {"parser": parser_name(p), "from": local_time(first, now) if first else None}
        for p, first in sorted(result["coverage"].items())
    ]

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
