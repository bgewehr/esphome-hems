"""Browse for _ship._tcp mDNS services on the local network.

Exits as soon as services are found (up to 6s max).
"""
import threading
import time
from zeroconf import Zeroconf, ServiceBrowser

found = []
done = threading.Event()

def on_service_state_change(zeroconf, service_type, name, state_change):
    found.append((name, state_change))
    done.set()

zc = Zeroconf()
sb = ServiceBrowser(zc, '_ship._tcp.local.', handlers=[on_service_state_change])
done.wait(timeout=6.0)
time.sleep(1.5)  # allow extra entries to trickle in after first hit

print(f'Found {len(found)} _ship._tcp services')
for name, sc in found:
    info = zc.get_service_info('_ship._tcp.local.', name)
    if info:
        print(f'  Name: {info.name}')
        print(f'  Host: {info.server}')
        print(f'  Port: {info.port}')
        props = {k.decode() if isinstance(k, bytes) else k:
                 v.decode() if isinstance(v, bytes) else v
                 for k, v in info.properties.items()}
        print(f'  SKI:  {props.get("ski", "")}')
        print(f'  Props: {props}')
    else:
        print(f'  {name} (no info)')
zc.close()
