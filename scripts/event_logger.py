import time
import json
import os
import psutil
import requests
import re
from datetime import datetime

# Try importing the required packages
try:
    import obsws_python as obs
except ImportError:
    obs = None

LOG_FILE = os.path.expanduser("~/.config/yasb/timeline_events.jsonl")

PRIORITIES = {
    "High": "🔴",
    "Medium": "🟡",
    "Low": "🟢",
    "Trace": "⚪"
}

def log_event(event_type: str, priority: str, timestamp: float = None, details: dict = None):
    if timestamp is None:
        timestamp = time.time()
        
    dt_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    emoji = PRIORITIES.get(priority, "⚪")
    
    entry = {
        "timestamp": timestamp,
        "datetime": dt_str,
        "event_type": event_type,
        "priority": priority,
    }
    if details:
        entry["details"] = details
        
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            
        color_code = "\033[91m" if priority == "High" else "\033[93m" if priority == "Medium" else "\033[92m"
        reset = "\033[0m"
        details_str = f" - {json.dumps(details, ensure_ascii=False)}" if details else ""
        print(f"[{dt_str}] {color_code}{emoji} [{priority}]{reset} {event_type}{details_str}")
    except Exception:
        pass


def get_youtube_start_time(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code != 200:
            return None
        match = re.search(r'ytInitialPlayerResponse\s*=\s*({.+?});(?:var|</script>)', response.text)
        if not match:
            return None
            
        player_response = json.loads(match.group(1))
        microformat = player_response.get("microformat", {}).get("playerMicroformatRenderer", {})
        live_details = microformat.get("liveBroadcastDetails", {})
        
        start_str = live_details.get("startTimestamp")
        if start_str:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            return dt.timestamp()
    except Exception:
        pass
    return None


def main():
    print(f"🚀 Starting Timeline Event Logger (Optimized)")
    print(f"📁 Log File: {LOG_FILE}\n")
    
    OBS_PASSWORD = "tsJvXCzzCGFdgxoq"
    YT_URL = "https://www.youtube.com/channel/UC-lHJZR3Gqxm24_Vd_AJ5Yw/live"
    
    # States
    is_obs_running = False
    is_obs_streaming = False
    is_obs_reconnecting = False
    last_yt_start = None
    obs_client = None

    # Init
    log_event("computer_startup", "High", timestamp=psutil.boot_time())

    # Polling loops decoupled
    loop_count = 0
    
    while True:
        try:
            # --- 1. Every 10 seconds: OBS Monitor ---
            if loop_count % 2 == 0:
                obs_proc_found = any('obs64.exe' in p.name().lower() for p in psutil.process_iter(['name']))
                        
                if obs_proc_found and not is_obs_running:
                    is_obs_running = True
                    log_event("obs_process_start", "Low")
                elif not obs_proc_found and is_obs_running:
                    is_obs_running = False
                    is_obs_streaming = False
                    is_obs_reconnecting = False
                    obs_client = None
                    log_event("obs_process_exit", "Low")

                if is_obs_running and obs:
                    try:
                        if not obs_client:
                            obs_client = obs.ReqClient(password=OBS_PASSWORD, timeout=3)
                        
                        status = obs_client.get_stream_status()
                        active = getattr(status, 'output_active', False)
                        reconnecting = getattr(status, 'output_reconnecting', False)
                        
                        if active and not is_obs_streaming:
                            is_obs_streaming = True
                            log_event("obs_stream_start", "Medium")
                            
                        if not active and is_obs_streaming:
                            if reconnecting and not is_obs_reconnecting:
                                is_obs_reconnecting = True
                                log_event("obs_stream_interruption", "Low")
                            elif not reconnecting:
                                is_obs_streaming = False
                                is_obs_reconnecting = False
                                log_event("obs_stream_stop", "Medium")
                                
                        if active and not reconnecting and is_obs_reconnecting:
                            is_obs_reconnecting = False
                            log_event("obs_stream_reconnect", "Low")

                    except Exception as e:
                        obs_client = None
                        if is_obs_streaming:
                            is_obs_streaming = False
                            is_obs_reconnecting = False
                            log_event("obs_stream_stop", "Medium", details={"reason": "websocket_error"})

            # --- 2. Every 120 seconds: YouTube Monitor ---
            if loop_count % 24 == 0:
                yt_start = get_youtube_start_time(YT_URL)
                if yt_start and not last_yt_start:
                    log_event("youtube_stream_start", "High", timestamp=yt_start)
                    last_yt_start = yt_start
                elif yt_start and last_yt_start and abs(yt_start - last_yt_start) > 60:
                    log_event("youtube_stream_start", "High", timestamp=yt_start, details={"note": "restarted"})
                    last_yt_start = yt_start
                elif not yt_start and last_yt_start:
                    log_event("youtube_stream_stop", "High")
                    last_yt_start = None
                    
        except Exception:
            pass
            
        loop_count += 1
        time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_event("computer_shutdown", "High", details={"note": "script_terminated"})
