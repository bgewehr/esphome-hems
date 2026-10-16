import os


def get_fronius_endpoint():
    host = os.environ.get("FRONIUS_HOST", "fronius.local")
    port = int(os.environ.get("FRONIUS_PORT", "502"))
    return host, port