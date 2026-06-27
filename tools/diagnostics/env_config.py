import os


def get_fronius_endpoint():
    host = os.environ.get("FRONIUS_HOST", "192.168.1.50")
    port = int(os.environ.get("FRONIUS_PORT", "502"))
    return host, port