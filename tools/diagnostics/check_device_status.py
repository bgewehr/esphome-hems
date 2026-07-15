"""Read selected live values from the ESPHome web server without caching."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.178.24")
    args = parser.parse_args()

    entities = {
        "Build Time": ("esphome_hems_build_time",),
        "EG1 Verbindungsstatus": ("esphome_hems_eg1_verbindungsstatus",),
        "EG1 Aktive UC": ("esphome_hems_eg1_aktive_uc",),
        "EG1 Unterstützte UC": (
            "esphome_hems_eg1_unterstutzte_uc",
            "esphome_hems_eg1_unterst_tzte_uc",
            "esphome_hems_eg1_untersttzte_uc",
            "esphome_hems_eg1_unterstzte_uc",
            "esphome_hems_eg1_unterstützte_uc",
        ),
    }
    for label, object_ids in entities.items():
        value = "not found"
        for object_id in object_ids:
            encoded_object_id = urllib.parse.quote(object_id)
            url = f"http://{args.host}/text_sensor/{encoded_object_id}?_={time.time_ns()}"
            request = urllib.request.Request(
                url,
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.load(response)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    continue
                raise
            value = payload.get("value", payload.get("state", payload))
            break
        print(f"{label}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())