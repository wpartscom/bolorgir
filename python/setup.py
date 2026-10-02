"""
Build script for the Bolorgir package.

Build order (from the python/ directory):

    # Full build: zig build ReleaseSafe -> copy .so into _lib -> abi3 extension
    python3 setup.py build_ext --inplace          # for development
    python3 -m pip wheel . --no-deps              # abi3 wheel (cp310)

Environment variables:
    ZIG             path to zig (default: zig from PATH)
    BLG_SKIP_ZIG=1  skip `zig build`, but sync _lib from BLG_CORE_LIB or
                    zig-out/lib if present (a clean CI used to fail: a
                    fresh .so sat in zig-out while _lib kept the stub)
    BLG_CORE_LIB     directory with a ready libbolorgir.so* to copy
                    into _lib when BLG_SKIP_ZIG=1
    BLG_SKIP_COPY=1  never touch _lib (sdist mode: the library is already
                    inside the package)
    BLG_ZIG_ARGS     extra `zig build` arguments (e.g. -Dcpu=baseline)

The sdist is self-contained: the sdist command prepares _lib (zig build
or a ready library, same rules as build_ext) BEFORE archiving, and the
C header is shipped inside the package as bolorgir/include/bolorgir.h
(synced from the repository root, git-ignored). A wheel built from the
unpacked sdist therefore needs neither zig nor the repository checkout.
A direct wheel build is covered the same way: build_py prepares _lib
before package data is copied (setuptools runs build_py before
build_ext).

The zig step tries `zig build -Doptimize=ReleaseSafe`; the project build.zig
declares preferred_optimize_mode, so its CLI is `-Drelease=true`
(ReleaseSafe as the preferred mode): if the first form is rejected, it falls
back to that one, then to a plain `zig build`.

The bolorgir._core extension is built with the Limited API
(Py_LIMITED_API=0x030A0000) and linked against libbolorgir from
python/bolorgir/_lib with rpath $ORIGIN/_lib -> abi3 wheel cp310.
"""

import filecmp
import os
import shutil
import subprocess
import sys

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)  # repository root (include/, zig-out/)
PKG_DIR = os.path.join(HERE, "bolorgir")
LIB_DIR = os.path.join(PKG_DIR, "_lib")
DEFAULT_ZIG = "zig"


def _copy_core_lib(src=None):
    src = src or os.path.join(ROOT, "zig-out", "lib")
    os.makedirs(LIB_DIR, exist_ok=True)
    copied = []
    for name in sorted(os.listdir(src)):
        if not name.startswith("libbolorgir.so"):
            continue
        sp = os.path.join(src, name)
        dp = os.path.join(LIB_DIR, name)
        if os.path.islink(sp):
            if os.path.lexists(dp):
                os.unlink(dp)
            os.symlink(os.readlink(sp), dp)
        elif os.path.isfile(sp):
            shutil.copy2(sp, dp)
        copied.append(name)
    if not copied:
        raise RuntimeError(
            f"libbolorgir.so* not found in {src}; "
            "run `zig build` in the repository root first"
        )
    return copied


def _core_lib_present():
    if not os.path.isdir(LIB_DIR):
        return False
    return any(
        name.startswith("libbolorgir.so")
        for name in os.listdir(LIB_DIR)
    )


def _copy_available_core_lib():
    """With BLG_SKIP_ZIG=1: copy a fresh .so if one exists somewhere.

    Priority: BLG_CORE_LIB -> zig-out/lib. BLG_SKIP_COPY=1 disables copying
    entirely (sdist with a ready _lib inside).
    """
    if os.environ.get("BLG_SKIP_COPY") == "1":
        return []
    explicit = os.environ.get("BLG_CORE_LIB")
    if explicit:
        if os.path.isfile(explicit):
            os.makedirs(LIB_DIR, exist_ok=True)
            name = os.path.basename(explicit)
            shutil.copy2(explicit, os.path.join(LIB_DIR, name))
            return [name]
        return _copy_core_lib(explicit)
    default_src = os.path.join(ROOT, "zig-out", "lib")
    if os.path.isdir(default_src) and any(
        name.startswith("libbolorgir.so")
        for name in os.listdir(default_src)
    ):
        return _copy_core_lib(default_src)
    return []


def _resolve_zig():
    """ZIG env -> PATH -> default `zig`; clear error otherwise."""
    env = os.environ.get("ZIG")
    if env:
        return env
    found = shutil.which("zig")
    if found:
        return found
    raise RuntimeError(
        "zig not found: set the ZIG environment variable, install zig into "
        "PATH, or build the library in advance (zig build -Drelease=true) "
        "and set BLG_SKIP_ZIG=1"
    )


def _run_zig_build():
    zig = _resolve_zig()
    extra = os.environ.get("BLG_ZIG_ARGS", "").split()
    attempts = [
        [zig, "build", "-Doptimize=ReleaseSafe", *extra],
        [zig, "build", "-Drelease=true", *extra],  # preferred_optimize_mode = ReleaseSafe
        [zig, "build", *extra],
    ]
    last_err = None
    for cmd in attempts:
        try:
            subprocess.check_call(cmd, cwd=ROOT)
            return
        except (subprocess.CalledProcessError, OSError) as e:
            # -Doptimize is rejected by this build.zig (falls through to
            # -Drelease=true); a missing executable lands here too.
            last_err = e
            print(f"bolorgir: {' '.join(cmd)} failed, trying next")
    raise RuntimeError(f"zig build failed: {last_err}")


def _zig_project_available():
    return os.path.isfile(os.path.join(ROOT, "build.zig"))


_prepared = False


def _prepare_core_lib():
    """Make sure bolorgir/_lib holds libbolorgir.so*.

    Shared by build_py, build_ext and sdist so that `python -m build`
    archives an sdist that already contains the binary; the wheel built
    from that sdist then works without zig and without the repository
    checkout. Runs once per setup() invocation.
    """
    global _prepared
    if _prepared:
        return
    if os.environ.get("BLG_SKIP_ZIG") == "1":
        copied = _copy_available_core_lib()
        if copied:
            print("bolorgir: _lib updated from a ready build:",
                  ", ".join(copied))
        if not _core_lib_present():
            raise RuntimeError(
                "BLG_SKIP_ZIG=1, but libbolorgir.so* not found: "
                "run `zig build -Drelease=true` in the repository root "
                "or set BLG_CORE_LIB to a directory containing the library"
            )
        _prepared = True
        return
    if not _zig_project_available():
        if _core_lib_present():
            _prepared = True
            return  # unpacked sdist: use the bundled binary
        raise RuntimeError(
            "repository root with build.zig not found and _lib has no "
            "bundled libbolorgir.so*; build from the repository checkout"
        )
    _run_zig_build()
    copied = _copy_core_lib()
    print("bolorgir: copied to _lib:", ", ".join(copied))
    _prepared = True


class BolorgirBuildPy(build_py):
    """Prepare _lib before package data is copied.

    setuptools runs build_py before build_ext, so in a direct wheel build
    (`pip wheel ./python`) the _lib payload would otherwise be copied
    before the zig step populated it.
    """

    def run(self):
        _prepare_core_lib()
        super().run()


class ZigBuildExt(build_ext):
    """build_ext with a preliminary zig build step + .so copy into _lib."""

    def run(self):
        _prepare_core_lib()
        super().run()


class BolorgirSdist(sdist):
    """sdist that ships a ready binary: _lib is prepared before archiving."""

    def run(self):
        _prepare_core_lib()
        super().run()


def _sync_readme():
    # The package long description lives at the repository root; setuptools
    # rejects readme files outside the project directory, so the build
    # syncs a copy next to setup.py (the copy is git-ignored).
    src = os.path.join(ROOT, "README.md")
    dst = os.path.join(HERE, "README.md")
    if not os.path.exists(src):
        return
    if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
        return
    shutil.copyfile(src, dst)


def _sync_header():
    # The C header lives at the repository root; the sdist must be
    # self-contained, so the build syncs a copy into the package (the
    # copy is git-ignored) and the extension always compiles against it.
    src = os.path.join(ROOT, "include", "bolorgir.h")
    dst = os.path.join(PKG_DIR, "include", "bolorgir.h")
    if not os.path.exists(src):
        return
    if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


_sync_readme()
_sync_header()

ext_modules = [
    Extension(
        "bolorgir._core",
        sources=[os.path.join("bolorgir", "_core.c")],
        include_dirs=[os.path.join(PKG_DIR, "include")],
        library_dirs=[LIB_DIR],
        libraries=["bolorgir"],
        runtime_library_dirs=["$ORIGIN/_lib"] if sys.platform != "darwin" else [],
        define_macros=[("Py_LIMITED_API", "0x030A0000")],
        py_limited_api=True,
    )
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_py": BolorgirBuildPy, "build_ext": ZigBuildExt,
              "sdist": BolorgirSdist},
    options={"bdist_wheel": {"py_limited_api": "cp310"}},
)
