# fetches the sources the two uploaded archives did not carry (they were GitHub zips: no git history, so
# no submodule pins and empty submodule folders).
#
#   py scripts/fetch-sources.py            Kyty's 3rdparty at the exact commit this tree was cut from,
#                                          FEX and libadrenotools at the commits sharpdroid pins
#   py scripts/fetch-sources.py --only kyty|sharpdroid
#
# exact pins come from cloning the upstream repositories *with* their submodules and copying the
# checked-out folders over the empty ones here. needs git and a network; nothing else.
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KYTY_URL, KYTY_COMMIT = "https://github.com/KytyPS5/KytyPS5.git", "fbea51c"
SHARPDROID_URL = "https://github.com/mircowuffwuff/sharpdroid.git"


def run(*command, cwd=None):
    print("+ " + " ".join(str(c) for c in command))
    subprocess.run([str(c) for c in command], check=True, cwd=cwd)


def copy_missing(source, target):
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        if entry.name == ".git":
            continue
        destination = target / entry.name
        if destination.is_dir() and any(destination.iterdir()):
            continue  # already populated
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(entry, destination, symlinks=True,
                        ignore=shutil.ignore_patterns(".git"))
        print("  filled " + str(destination.relative_to(ROOT)))


def kyty():
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "kyty"
        run("git", "clone", "--filter=blob:none", "--no-checkout", KYTY_URL, clone)
        run("git", "checkout", KYTY_COMMIT, cwd=clone)
        run("git", "submodule", "update", "--init", "--recursive", "--filter=blob:none", cwd=clone)
        copy_missing(clone / "3rdparty", ROOT / "external" / "kytyps5" / "3rdparty")


def sharpdroid():
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "sharpdroid"
        run("git", "clone", "--depth", "1", "--recurse-submodules", "--shallow-submodules",
            "--filter=blob:none", SHARPDROID_URL, clone)
        for name in ("FEX", "libadrenotools"):
            copy_missing(clone / "external" / name, ROOT / "external" / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=("kyty", "sharpdroid"))
    arguments = parser.parse_args()
    if shutil.which("git") is None:
        sys.exit("fetch-sources: git is required")
    if arguments.only in (None, "kyty"):
        kyty()
    if arguments.only in (None, "sharpdroid"):
        sharpdroid()
    print("done")


if __name__ == "__main__":
    main()
