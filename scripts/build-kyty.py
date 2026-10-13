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
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KYTY = ROOT / "external" / "kytyps5"
OUT = ROOT / "build" / "kyty"

PACKAGES = ("cmake ninja-build git glslang-tools python3 pkg-config ca-certificates wget gnupg "
            "lsb-release software-properties-common libc6-dev g++ make")


def die(message):
    print("build-kyty: " + message, file=sys.stderr)
    sys.exit(1)


def configure_and_build(jobs):
    cflags = "-march=x86-64 -mtune=generic"
    build_dir = OUT / "cmake"
    steps = [
        ["cmake", "-S", str(KYTY), "-B", str(build_dir), "-G", "Ninja",
         "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
         "-DKYTY_ANDROID_GUEST=ON", "-DKYTY_BUILD_LAUNCHER=OFF", "-DKYTY_LITE_BUILD=ON",
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
