import os


def get_fronius_endpoint():
    host = os.environ.get("FRONIUS_HOST", "192.168.1.50")
    port = int(os.environ.get("FRONIUS_PORT", "502"))
    return host, port


def get_eebus_endpoint():
    host = os.environ.get("EEBUS_BRIDGE_HOST", "192.168.1.60")
    port = int(os.environ.get("EEBUS_BRIDGE_PORT", "50051"))
    return host, port


def get_eebus_ski():
    ski = os.environ.get("EEBUS_DEVICE_SKI", "")
    if not ski:
        raise SystemExit("Set EEBUS_DEVICE_SKI before running this diagnostic script.")
    return ski
