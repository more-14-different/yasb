import time
import json
import os
import psutil
import requests
import re
from datetime import datetime

import obsws_python as obs

LOG_FILE = os.path.expanduser("~/.config/yasb/timeline_events.jsonl")

def log_event(event_type, priority, timestamp=None, details=None):
    if timestamp is None:
        timestamp = time.time()
        
    entry = {
        "timestamp": timestamp,
        "datetime": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S"),
        "event_type": event_type,
        "priority": priority,
    }
    if details:
        entry["details"] = details
        
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[{entry['datetime']}] [{priority}] {event_type} {details or ''}")
    except Exception as e:
        print(f"Failed to log event: {e}")


def get_youtube_start_time(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
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
    print(f"Starting Timeline Event Logger. Logging to {LOG_FILE}")
    
    # Configuration
    # Read yasb config for OBS password if needed, or hardcode here.
    OBS_PASSWORD = "tsJvXCzzCGFdgxoq"
    YT_URL = "https://www.youtube.com/channel/UC-lHJZR3Gqxm24_Vd_AJ5Yw/live"
    
    # State tracking
    last_boot_time = psutil.boot_time()
    log_event("computer_startup", "High", timestamp=last_boot_time)
    
    is_obs_running = False
    is_obs_streaming = False
    is_obs_reconnecting = False
    
    last_yt_start = None
    yt_check_counter = 0
    
    obs_client = None

    while True:
        try:
            now = time.time()
            
            # 1. OBS Process Status
            obs_proc_found = False
            for p in psutil.process_iter(['name']):
                if p.info['name'] and 'obs64.exe' in p.info['name'].lower():
                    obs_proc_found = True
                    break
                    
            if obs_proc_found and not is_obs_running:
                is_obs_running = True
                log_event("obs_process_start", "Low")
            elif not obs_proc_found and is_obs_running:
                is_obs_running = False
                is_obs_streaming = False
                is_obs_reconnecting = False
                obs_client = None
                log_event("obs_process_exit", "Low")

            # 2. OBS WebSocket Status
            if is_obs_running:
                try:
                    if not obs_client:
                        obs_client = obs.ReqClient(password=OBS_PASSWORD, timeout=3)
                    
                    status = obs_client.get_stream_status()
                    active = getattr(status, 'output_active', False)
                    reconnecting = getattr(status, 'output_reconnecting', False)
                    timecode = getattr(status, 'output_timecode', "")
                    
                    if active and not is_obs_streaming:
                        is_obs_streaming = True
                        log_event("obs_stream_start", "Medium", details={"timecode": timecode})
                        
                    if not active and is_obs_streaming:
                        if reconnecting:
                            if not is_obs_reconnecting:
                                is_obs_reconnecting = True
                                log_event("obs_stream_interruption", "Low")
                        else:
                            is_obs_streaming = False
                            is_obs_reconnecting = False
                            log_event("obs_stream_stop", "Medium")
                            
                    if active and not reconnecting and is_obs_reconnecting:
                        is_obs_reconnecting = False
                        log_event("obs_stream_reconnect", "Low")

                except Exception as e:
                    # WebSocket disconnected or failed
                    obs_client = None
                    if is_obs_streaming:
                        is_obs_streaming = False
                        is_obs_reconnecting = False
                        log_event("obs_stream_stop", "Medium", details={"reason": "websocket_error", "error": str(e)})

            # 3. YouTube Stream Status (Poll every 60 seconds)
            if yt_check_counter % 12 == 0:
                yt_start = get_youtube_start_time(YT_URL)
                
                if yt_start and not last_yt_start:
                    log_event("youtube_stream_start", "High", timestamp=yt_start)
                    last_yt_start = yt_start
                elif yt_start and last_yt_start and abs(yt_start - last_yt_start) > 60:
                    # Stream restarted on YouTube
                    log_event("youtube_stream_start", "High", timestamp=yt_start, details={"note": "restarted"})
                    last_yt_start = yt_start
                elif not yt_start and last_yt_start:
                    log_event("youtube_stream_stop", "High")
                    last_yt_start = None
                    
            yt_check_counter += 1
            
        except Exception as main_e:
            print(f"Main loop error: {main_e}")
            
        # Sleep 5 seconds
        time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_event("computer_shutdown", "High", details={"note": "script_terminated"})
