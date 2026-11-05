#!/usr/bin/env python3
"""Rebuild avail.json from the Airbnb iCal feeds.

The feed list is read from the AIRBNB_ICALS environment variable (one entry
per line: "CODE <listing-id>.ics?t=<token>").  Those URLs are private: this
script never prints them and never writes them into avail.json.

Output: avail.json at the repo root, compact, shaped as

    {"updated":"YYYY-MM-DDTHH:MMZ","busy":{"CODE":[["start","endExclusive"],...]}}

Exit codes
    0  finished; avail.json is correct
    1  too many feeds failed (see MAX_FAILED) -- avail.json was still written
    2  configuration problem (no feed list, unparseable list)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

FEED_BASE = "https://www.airbnb.com/calendar/ical/"
USER_AGENT = "RK-Availability-Sync/1.0"
TIMEOUT = 45
RETRIES = 2  # one attempt plus one retry, per the original brief


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_feed_list(raw: str) -> list[tuple[str, str]]:
    """Turn the secret's text into [(code, path), ...]."""
    feeds: list[tuple[str, str]] = []
    seen: set[str] = set()
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            log(f"::error::feed list line {lineno} is not 'CODE path'")
            sys.exit(2)
        code, path = parts
        if code in seen:
            log(f"::error::duplicate code {code} on feed list line {lineno}")
            sys.exit(2)
        seen.add(code)
        feeds.append((code, path))
    return feeds


def fetch(path: str) -> str:
    """Download one feed. Raises on failure."""
    req = urllib.request.Request(
        FEED_BASE + path, headers={"User-Agent": USER_AGENT, "Accept": "text/calendar"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_with_retry(code: str, path: str) -> str | None:
    last = ""
    for attempt in range(1, RETRIES + 1):
        try:
            return fetch(path)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            # Deliberately report the code and the error class only -- never the URL.
            last = type(exc).__name__
            if isinstance(exc, urllib.error.HTTPError):
                last = f"HTTP {exc.code}"
            if attempt < RETRIES:
                time.sleep(3)
    log(f"::warning::{code}: download failed ({last})")
    return None


def unfold(text: str) -> list[str]:
    """Undo RFC 5545 line folding."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def prop_date(value: str) -> str | None:
    """'20261105' or '20261105T140000Z' -> '2026-11-05'."""
    digits = value.strip().split("T", 1)[0]
    if len(digits) != 8 or not digits.isdigit():
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])).isoformat()
    except ValueError:
        return None


def parse_ranges(text: str) -> list[list[str]]:
    """Keep only DTSTART/DTEND of each VEVENT. Everything else is ignored."""
    ranges: list[list[str]] = []
    start = end = None
    in_event = False
    for line in unfold(text):
        head, _, value = line.partition(":")
        name = head.split(";", 1)[0].strip().upper()
        if name == "BEGIN" and value.strip().upper() == "VEVENT":
            in_event, start, end = True, None, None
        elif name == "END" and value.strip().upper() == "VEVENT":
            if start and end and start < end:
                ranges.append([start, end])
            in_event, start, end = False, None, None
        elif in_event and name == "DTSTART":
            start = prop_date(value)
        elif in_event and name == "DTEND":
            end = prop_date(value)
    return ranges


def tidy(ranges: list[list[str]], today: str) -> list[list[str]]:
    """Drop fully past ranges, then sort and merge overlapping or touching ones."""
    live = [r for r in ranges if r[1] > today]
    if not live:
        return []
    live.sort()
    merged = [list(live[0])]
    for start, end in live[1:]:
        if start <= merged[-1][1]:  # overlapping or touching
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged


def main() -> int:
    raw = os.environ.get("AIRBNB_ICALS", "").strip()
    if not raw:
        log("::error::AIRBNB_ICALS is empty -- add the feed list as a repository secret")
        return 2

    feeds = parse_feed_list(raw)
    if not feeds:
        log("::error::AIRBNB_ICALS contained no feeds")
        return 2

    out_path = os.environ.get("AVAIL_PATH", "avail.json")
    max_failed = int(os.environ.get("MAX_FAILED", "2"))
    today = datetime.now(timezone.utc).date().isoformat()

    previous: dict[str, list[list[str]]] = {}
    try:
        with open(out_path, encoding="utf-8") as fh:
            previous = json.load(fh).get("busy", {}) or {}
    except (OSError, ValueError):
        log("note: no readable existing avail.json; starting fresh")

    busy: dict[str, list[list[str]]] = {}
    failed: list[str] = []

    for code, path in feeds:
        text = fetch_with_retry(code, path)
        if text is None:
            failed.append(code)
            # Keep whatever this code had before rather than dropping it.
            busy[code] = tidy([list(r) for r in previous.get(code, [])], today)
            continue
        busy[code] = tidy(parse_ranges(text), today)

    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "busy": busy,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")

    changed = busy != previous
    log(f"feeds={len(feeds)} failed={len(failed)} changed={'yes' if changed else 'no'}")
    if failed:
        log("failed codes: " + ", ".join(failed))

    step_out = os.environ.get("GITHUB_OUTPUT")
    if step_out:
        with open(step_out, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
            fh.write(f"failed={len(failed)}\n")
            fh.write(f"total={len(feeds)}\n")

    if len(failed) > max_failed:
        log(f"::error::{len(failed)} feeds failed (limit {max_failed})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
