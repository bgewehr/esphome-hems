"""
Sync patches/ to the two ESPHome/PlatformIO build locations.

Run via:   python scripts/sync_patches.py
Or automatically via:  compile.ps1

Two sync targets:
  patches/openeebus/src/**             →  .esphome/external_components/<hash>/openeebus/src/
  patches/openeebus/components/eebus_wp/**  →  c:/temp/esphome-hems/src/esphome/components/eebus_wp/
"""

import pathlib
import shutil
import sys

HEMS = pathlib.Path(__file__).resolve().parent.parent
patches_src = HEMS / "patches/openeebus/src"
patches_wp  = HEMS / "patches/openeebus/components/eebus_wp"
tmp_wp      = pathlib.Path("C:/temp/esphome-hems/src/esphome/components/eebus_wp")

tag = "[sync_patches]"
errors = 0

# ── 1. openeebus C source → external_components cache
ext_base = HEMS / ".esphome/external_components"
ext_src  = None
for match in sorted(ext_base.glob("*/openeebus/src")):
    ext_src = match
    break

if ext_src:
    hash_name = ext_src.parent.parent.name
    print(f"{tag} Syncing patches/openeebus/src -> external_components/{hash_name}/openeebus/src/")
    for src_file in patches_src.rglob("*"):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(patches_src)
        dst = ext_src / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src_file), str(dst))
        print(f"{tag}   {rel}")
else:
    print(f"{tag} WARNING: openeebus external_components not found — C source patches NOT synced")
    errors += 1

# ── 2. eebus_wp component → build directory
if tmp_wp.exists():
    print(f"{tag} Syncing patches/openeebus/components/eebus_wp -> {tmp_wp}")
    for src_file in patches_wp.iterdir():
        if src_file.is_file():
            shutil.copy2(str(src_file), str(tmp_wp / src_file.name))
            print(f"{tag}   {src_file.name}")
else:
    print(f"{tag} WARNING: build dir {tmp_wp} does not exist")
    print(f"{tag}   Run 'esphome compile esphome-hems.yaml' once first to create it, then rerun.")
    errors += 1

if errors:
    sys.exit(1)
print(f"{tag} Done.")
