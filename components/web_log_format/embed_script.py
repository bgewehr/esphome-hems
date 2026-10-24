import gzip
import re
from pathlib import Path


def embed_script(header):
    pattern = r"(\bINDEX_GZ\s*\[\s*\]\s*[^=]*=\s*\{)([^}]+)(\})"
    match = re.search(pattern, header)
    if match is None:
        raise ValueError("Local web-server INDEX_GZ array not found")
    compressed = bytes(int(value.strip(), 0) for value in match[2].split(",") if value.strip())
    html = gzip.decompress(compressed).decode("utf-8")
    script = '<script type="module" src="/0.js"></script>'
    if script in html:
        return header
    if "</body>" not in html:
        raise ValueError("Local web-server HTML has no closing body")
    html = html.replace("</body>", script + "</body>", 1)
    packed = gzip.compress(html.encode("utf-8"), mtime=0)
    values = ",".join(str(value) for value in packed)
    return header[:match.start(2)] + values + header[match.end(2):]


if "Import" in globals():
    Import("env")
    header_path = Path(env.subst("$PROJECT_SRC_DIR")) / "esphome/components/web_server/server_index_v3.h"
    original = header_path.read_text(encoding="utf-8")
    updated = embed_script(original)
    if updated != original:
        header_path.write_text(updated, encoding="utf-8")