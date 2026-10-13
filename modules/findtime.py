#!/usr/bin/env python3
"""
time_find.py - Simple script to fetch the current time from the internet.
Uses the Python standard library without requiring external dependencies.
"""

import email.utils
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from core.modkit import script


def get_internet_time() -> datetime:
    """Fetch the current UTC datetime from the internet."""
    # Primary source: timeapi.io JSON API
    api_url = "https://timeapi.io/api/time/current/zone?timeZone=UTC"
    try:
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "time_find.py/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            return datetime.fromisoformat(data["dateTime"]).replace(tzinfo=timezone.utc)
    except Exception:
        # Fallback source: HTTP Date header from a reliable server
        req = urllib.request.Request(
            "https://www.google.com",
            headers={"User-Agent": "time_find.py/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            date_str = response.headers.get("Date")
            if not date_str:
                raise RuntimeError("Could not retrieve Date header.")
            return email.utils.parsedate_to_datetime(date_str)


def main():
    try:
        print("Fetching current time from the internet...")
        utc_time = get_internet_time()
        local_time = utc_time.astimezone()

        print("-" * 40)
        print(f"UTC Time:   {utc_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Local Time: {local_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("-" * 40)
    except urllib.error.URLError as e:
        print(f"Network error: Unable to reach the internet ({e.reason})")
    except Exception as e:
        print(f"Error fetching internet time: {e}")


@script("findtime")
def run(args: list[str], stdin: str) -> str:
    main()
