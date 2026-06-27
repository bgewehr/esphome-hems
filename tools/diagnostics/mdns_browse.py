"""Browse for _ship._tcp mDNS services on the local network."""
from zeroconf import Zeroconf, ServiceBrowser
import time

found = []

def on_service_state_change(zeroconf, service_type, name, state_change):
    found.append((name, state_change))

zc = Zeroconf()
sb = ServiceBrowser(zc, '_ship._tcp.local.', handlers=[on_service_state_change])
time.sleep(8)
print(f'Found {len(found)} _ship._tcp services')
for name, sc in found:
    info = zc.get_service_info('_ship._tcp.local.', name)
    if info:
        print(f'  Name: {info.name}')
        print(f'  Host: {info.server}')
        print(f'  Port: {info.port}')
        print(f'  Props: {info.properties}')
    else:
        print(f'  {name} (no info)')
zc.close()
