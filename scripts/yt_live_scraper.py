import json
import os
import re
import sys
import time
from datetime import datetime
from typing import TypedDict, cast

import requests  # pyright: ignore[reportMissingModuleSource]


class SimpleText(TypedDict, total=False):
    simpleText: str


class LiveBroadcastDetails(TypedDict, total=False):
    startTimestamp: str


class PlayerMicroformatRenderer(TypedDict, total=False):
    liveBroadcastDetails: LiveBroadcastDetails
    title: SimpleText


class Microformat(TypedDict, total=False):
    playerMicroformatRenderer: PlayerMicroformatRenderer


class PlayerResponse(TypedDict, total=False):
    microformat: Microformat


class YouTubeLiveInfo(TypedDict):
    url: str
    startTimestamp: str
    epoch: float
    fetched_at: float
    title: str


def fetch_yt_start_time(url: str) -> None:
    print(f"Fetching {url} ...")
    try:
        # Use a generic user agent to prevent basic blocking
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Error fetching URL: {e}")
        return

    html = response.text

    # Extract ytInitialPlayerResponse
    match = re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});(?:var|</script>)", html)
    if not match:
        print("Could not find ytInitialPlayerResponse in page.")
        return

    player_response_str = match.group(1)
    try:
        player_response = cast(PlayerResponse, json.loads(player_response_str))
    except json.JSONDecodeError:
        print("Failed to parse ytInitialPlayerResponse JSON.")
        return

    microformat = player_response.get("microformat", {}).get("playerMicroformatRenderer", {})
    live_details = microformat.get("liveBroadcastDetails", {})

    start_timestamp_str = live_details.get("startTimestamp")

    if not start_timestamp_str:
        print("No startTimestamp found. Is the channel currently live?")
        return

    try:
        # startTimestamp is usually ISO8601 like "2026-07-07T14:00:00+00:00"
        # Replace Z with +00:00 for older python versions
        start_timestamp_str_fixed = start_timestamp_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(start_timestamp_str_fixed)
        epoch = dt.timestamp()
    except Exception as e:
        print(f"Failed to parse timestamp {start_timestamp_str}: {e}")
        return

    result: YouTubeLiveInfo = {
        "url": url,
        "startTimestamp": start_timestamp_str,
        "epoch": epoch,
        "fetched_at": time.time(),
        "title": microformat.get("title", {}).get("simpleText", "Unknown"),
    }

    print(json.dumps(result, indent=2))

    # Save to ~/.config/yasb/yt_live_info.json
    out_file = os.path.expanduser("~/.config/yasb/yt_live_info.json")
    try:
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"Saved to {out_file}")
    except Exception as e:
        print(f"Failed to save to file: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        # Change this to your channel's live URL
        url = "https://www.youtube.com/channel/UC-lHJZR3Gqxm24_Vd_AJ5Yw/live"
    fetch_yt_start_time(url)
