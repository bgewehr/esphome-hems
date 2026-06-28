#!/usr/bin/env python3
"""Apply local patches to ESPHome external_components cache.

Copies modified files from patches/ to the corresponding location in
.esphome/external_components/. Run this after `esphome compile` refreshes
the external components cache, or after a clean checkout.

Usage:
    python tools/apply_patches.py
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCHES_DIR = REPO_ROOT / "patches"
EXTERNAL_COMPONENTS = REPO_ROOT / ".esphome" / "external_components"

# Mapping: patch subdirectory -> external_components hash directory
# The hash b72b2cfd corresponds to github://bgewehr/openeebus-esphome@main
PATCH_TARGETS = {
    "openeebus": "b72b2cfd",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _init_submodules(target_dir: Path) -> None:
    """Initialize git submodules in target_dir if any are uninitialized.

    ESPHome's external_component checkout does 'git clone --depth 1' without
    '--recurse-submodules', so submodules are always left uninitialised.
    This must run before copying patch files so the submodule directories exist.
    """
    gitmodules = target_dir / ".gitmodules"
    if not gitmodules.exists():
        return

    result = subprocess.run(
        ["git", "submodule", "status"],
        cwd=target_dir,
        capture_output=True,
        text=True,
    )
    uninit = [line for line in result.stdout.splitlines() if line.startswith("-")]
    if not uninit:
        return

    print(f"  Initialising {len(uninit)} git submodule(s) in {target_dir.name}...")
    for line in uninit:
        name = line.split()[-1]
        sub_path = target_dir / name
        # Remove any stray files that would block the clone
        if sub_path.exists() and any(sub_path.iterdir()):
            shutil.rmtree(sub_path)
            sub_path.mkdir()
        print(f"    cloning submodule: {name}")

    subprocess.run(
        ["git", "submodule", "update", "--init"],
        cwd=target_dir,
        check=True,
    )
    print(f"  Submodules ready.")


def apply_patches() -> int:
    """Copy patch files to target locations, skipping identical files. Returns error count."""
    errors = 0

    for patch_name, target_hash in PATCH_TARGETS.items():
        patch_dir = PATCHES_DIR / patch_name
        target_dir = EXTERNAL_COMPONENTS / target_hash

        if not patch_dir.exists():
            print(f"  SKIP {patch_name}: patch directory not found")
            continue

        if not target_dir.exists():
            print(f"  WARN {patch_name}: target {target_dir} not found")
            print(f"       Run 'esphome compile' first to populate the cache.")
            errors += 1
            continue

        _init_submodules(target_dir)

        for patch_file in patch_dir.rglob("*"):
            if patch_file.is_dir():
                continue
            relative = patch_file.relative_to(patch_dir)
            dest = target_dir / relative

            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and _sha256(dest) == _sha256(patch_file):
                print(f"  --   {relative}  (unchanged, skip)")
                continue
            shutil.copy2(patch_file, dest)
            print(f"  OK   {relative}")

    return errors


def main():
    print(f"Applying patches from: {PATCHES_DIR}")
    print(f"Target: {EXTERNAL_COMPONENTS}\n")

    if not PATCHES_DIR.exists():
        print("No patches/ directory found.")
        return 0

    errors = apply_patches()

    if errors:
        print(f"\n{errors} error(s) — some patches could not be applied.")
        return 1
    else:
        print("\nAll patches applied successfully.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
