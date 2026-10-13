# builds the KytyPS5 payload that sharpdroid runs: a Linux x86-64 ELF, executed as guest code under FEX.
# this is a CI step: .github/workflows/build.yml runs it. it also works on any machine with docker.
#
#   py scripts/build-kyty.py --package      build inside a debian:12 container, then package the zip
#
# why debian 12: the guest runs against the glibc/libstdc++ set scripts/fetch-guest-libs.py stages
# (debian 12, glibc 2.36). a binary linked on a newer distro references newer symbol versions and the
# guest's ld.so refuses it. libstdc++/libgcc are linked statically (Kyty already does that).
#
# the CPU baseline is plain x86-64: FEXCore translates it, and -march=native would bake the build
# machine's extensions into guest code the phone may not support.
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KYTY = ROOT / "external" / "kytyps5"
OUT = ROOT / "build" / "kyty"

PACKAGES = ("cmake ninja-build git glslang-tools python3 pkg-config ca-certificates wget gnupg "
            "lsb-release software-properties-common libc6-dev g++ make")


def die(message):
    print("build-kyty: " + message, file=sys.stderr)
    sys.exit(1)


FFMPEG_RELEASE_DEFAULT = "9ac4cfd195f1"  # the ext-ffmpeg-core release the pinned submodule corresponds to


def prepare_ffmpeg():
    """ffmpeg-core picks its prebuilt release with `git rev-parse` inside its own folder. here that
    folder has no .git of its own, so git would answer with *this* repository's commit and the
    download would 404. FFMPEG_PREBUILT_DIR is ffmpeg-core's own switch for an unpacked package."""
    pins = KYTY / "3rdparty" / ".pins.json"
    release = FFMPEG_RELEASE_DEFAULT
    if pins.exists():
        release = json.loads(pins.read_text()).get("ffmpeg-core", release)
    target = OUT / "ffmpeg" / release
    if not (target / "include" / "libavcodec" / "avcodec.h").exists():
        url = "https://github.com/KytyPS5/ext-ffmpeg-core/releases/download/{}/ffmpeg-linux-x64.zip".format(release)
        archive = OUT / "ffmpeg" / (release + ".zip")
        archive.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, 5):
            try:
                print("downloading " + url, flush=True)
                request = urllib.request.Request(url, headers={"User-Agent": "sharpdroid-kyty-build"})
                with urllib.request.urlopen(request, timeout=120) as response, open(archive, "wb") as out:
                    shutil.copyfileobj(response, out)
                break
            except Exception as error:  # network hiccups are common on runners
                print("  attempt {} failed: {}".format(attempt, error), flush=True)
                if attempt == 4:
                    die("could not download " + url)
                time.sleep(5 * attempt)
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    if not (target / "include" / "libavcodec" / "avcodec.h").exists():
        die("the FFmpeg package at {} has no include/libavcodec/avcodec.h".format(target))
    return target


def configure_and_build(jobs):
    cflags = "-march=x86-64 -mtune=generic"
    build_dir = OUT / "cmake"
    ffmpeg = prepare_ffmpeg()
    steps = [
        ["cmake", "-S", str(KYTY), "-B", str(build_dir), "-G", "Ninja",
         "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
         "-DKYTY_ANDROID_GUEST=ON", "-DFFMPEG_PREBUILT_DIR=" + str(ffmpeg), "-DKYTY_BUILD_LAUNCHER=OFF", "-DKYTY_LITE_BUILD=ON",
         "-DKYTY_BUILD_ORIGIN=Fork", "-DKYTY_BUILD_REPOSITORY=sharpdroid-kyty",
         "-DCMAKE_C_FLAGS=" + cflags, "-DCMAKE_CXX_FLAGS=" + cflags,
         "-DCMAKE_EXE_LINKER_FLAGS=-static-libgcc -static-libstdc++",
         "-DCMAKE_JOB_POOLS=link=1", "-DCMAKE_JOB_POOL_LINK=link"],
        ["cmake", "--build", str(build_dir), "--target", "kyty_emulator", "--parallel", str(jobs)],
    ]
    for command in steps:
        print("+ " + " ".join(command), flush=True)
        subprocess.run(command, check=True)
    binary = next(build_dir.rglob("kyty_emulator"), None)
    if binary is None or not binary.is_file():
        die("kyty_emulator was not produced")
    stage = OUT / "payload"
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "plugins").mkdir(parents=True)
    shutil.copy2(binary, stage / "kyty_emulator")
    (stage / "plugins" / "readme.txt").write_text(
        "KytyPS5 keeps nothing here; the directory exists because the build format requires it.\n")
    shutil.copy2(KYTY / "LICENSE", stage / "KYTY-LICENSE")
    print("payload: " + str(stage))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", action="store_true", help="already inside debian 12 + clang")
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--inner", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    arguments = parser.parse_args()

    if not (KYTY / "3rdparty" / "SDL3" / "CMakeLists.txt").exists():
        die("Kyty's third-party sources are missing. run: python3 scripts/fetch-sources.py")

    if arguments.inner or arguments.native:
        configure_and_build(arguments.jobs)
    else:
        engine = shutil.which("docker") or shutil.which("podman")
        if not engine:
            die("docker or podman is required (GitHub's ubuntu runners have docker)")
        script = (
            "set -e; apt-get update; apt-get install -y --no-install-recommends " + PACKAGES + "; "
            "git config --global --add safe.directory '*'; "
            "wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh; chmod +x /tmp/llvm.sh; /tmp/llvm.sh 18; "
            "update-alternatives --install /usr/bin/clang clang /usr/bin/clang-18 100; "
            "update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-18 100; "
            "update-alternatives --install /usr/bin/ld.lld ld.lld /usr/bin/ld.lld-18 100; "
            "cd /src && python3 scripts/build-kyty.py --inner --jobs " + str(arguments.jobs) + "; "
            "chmod -R a+rwX /src/build")
        print("+ " + Path(engine).name + " run debian:12 ...", flush=True)
        subprocess.run([engine, "run", "--rm", "-v", str(ROOT) + ":/src", "-w", "/src", "debian:12",
                        "bash", "-c", script], check=True)

    if arguments.package:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "package-kyty.py")], check=True)


if __name__ == "__main__":
    main()
