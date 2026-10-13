# turns the payload built by build-kyty.py into a sharpdroid build: a directory and a zip.
#
#   py scripts/package-kyty.py [--id kyty] [--author NAME]
#
# the format is docs/build-format.md's, plus one field: "family": "kyty", which makes the app launch it
# with KYTY_* environment and `--game <dir>` instead of SharpEmu's. meta.json sits at the zip root.
import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "build" / "kyty" / "payload"
BUILDS = ROOT / "build" / "builds"
HOST_CONTRACT = 4


def git(*args):
    try:
        return subprocess.run(["git", "-C", str(ROOT / "external" / "kytyps5"), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default="kyty")
    parser.add_argument("--name", default="KytyPS5")
    parser.add_argument("--author", default="")
    parser.add_argument("--commit", default="fbea51c")
    arguments = parser.parse_args()
    if not (PAYLOAD / "kyty_emulator").exists():
        sys.exit("package-kyty: nothing to package; run scripts/build-kyty.py first")

    version = "0.3.0"
    stamp = int(datetime.now().strftime("%Y%m%d%H%M%S"))
    meta = {
        "id": arguments.id,
        "name": arguments.name,
        "family": "kyty",
        "sharpemuVersion": version,
        "packagedAt": stamp,
        "hostContract": HOST_CONTRACT,
        "payload": "kyty_emulator",
        "env": {},
        "notes": "KytyPS5 (Vulkan 1.3 renderer) running under FEX.",
        "author": arguments.author,
        "commit": arguments.commit,
        "source": "https://github.com/KytyPS5/KytyPS5",
    }
    folder = re.sub(r"[^a-z0-9._-]", "_", "{}-{}-{}".format(arguments.id, version, stamp).lower())
    target = BUILDS / folder
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(PAYLOAD, target)
    (target / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")  # UTF-8, no BOM
    archive = BUILDS / (folder + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(target).as_posix())
    print("build directory: " + str(target))
    print("importable zip:  " + str(archive))


if __name__ == "__main__":
    main()
