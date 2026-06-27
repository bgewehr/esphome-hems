"""Trigger ESPHome safe mode reboot via the button entity in the web server."""
import urllib.request
import sys

host = sys.argv[1] if len(sys.argv) > 1 else "192.168.178.24"
# ESPHome web_server v3 exposes a /button/safe_mode/press endpoint
url = f"http://{host}/button/safe_mode/press"
try:
    req = urllib.request.Request(url, method="POST", data=b"")
    resp = urllib.request.urlopen(req, timeout=5)
    print(f"Safe mode triggered: HTTP {resp.status}")
except Exception as e:
    print(f"Failed: {e}")
    # Try restart as fallback
    url2 = f"http://{host}/button/restart/press"
    try:
        req2 = urllib.request.Request(url2, method="POST", data=b"")
        resp2 = urllib.request.urlopen(req2, timeout=5)
        print(f"Restart triggered instead: HTTP {resp2.status}")
    except Exception as e2:
        print(f"Restart also failed: {e2}")
