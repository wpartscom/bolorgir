"""
Сборка пакета zig-constraints.

Порядок сборки (из каталога python/):

    # Полная сборка: zig build ReleaseSafe -> копия .so в _lib -> abi3-расширение
    python3 setup.py build_ext --inplace          # для разработки
    python3 -m pip wheel . --no-deps              # abi3 wheel (cp310)

Переменные окружения:
    ZIG             путь к zig (по умолчанию /home/gm/.local/bin/zig)
    ZG_SKIP_ZIG=1   пропустить `zig build` и копирование: использовать
                    уже лежащий python/zig_constraints/_lib/libzig_constraints.so*
                    (режим для sdist/повторной сборки с готовыми артефактами;
                    sdist после полной сборки содержит _lib и не требует zig)

Шаг zig: пробуется `zig build -Doptimize=ReleaseSafe`; build.zig проекта
объявляет preferred_optimize_mode, поэтому его CLI — `-Drelease=true`
(ReleaseSafe как предпочтительный режим): при отказе первой формы
выполняется fallback на неё, затем на plain `zig build`.

Расширение zig_constraints._core собирается с Limited API
(Py_LIMITED_API=0x030A0000) и линкуется с libzig_constraints из
python/zig_constraints/_lib с rpath $ORIGIN/_lib -> abi3 wheel cp310.
"""

import os
import shutil
import subprocess
import sys

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)  # корень репозитория (include/, zig-out/)
PKG_DIR = os.path.join(HERE, "zig_constraints")
LIB_DIR = os.path.join(PKG_DIR, "_lib")
DEFAULT_ZIG = "/home/gm/.local/bin/zig"


def _copy_core_lib():
    src = os.path.join(ROOT, "zig-out", "lib")
    os.makedirs(LIB_DIR, exist_ok=True)
    copied = []
    for name in sorted(os.listdir(src)):
        if not name.startswith("libzig_constraints.so"):
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
            f"libzig_constraints.so* не найдена в {src}; "
            "сначала выполните `zig build` в корне репозитория"
        )
    return copied


def _core_lib_present():
    if not os.path.isdir(LIB_DIR):
        return False
    return any(
        name.startswith("libzig_constraints.so")
        for name in os.listdir(LIB_DIR)
    )


def _run_zig_build():
    zig = os.environ.get("ZIG", DEFAULT_ZIG)
    attempts = [
        [zig, "build", "-Doptimize=ReleaseSafe"],
        [zig, "build", "-Drelease=true"],  # preferred_optimize_mode = ReleaseSafe
        [zig, "build"],
    ]
    last_err = None
    for cmd in attempts:
        try:
            subprocess.check_call(cmd, cwd=ROOT)
            return
        except subprocess.CalledProcessError as e:
            last_err = e
            print(f"zig_constraints: {' '.join(cmd)} не удался, пробую дальше")
    raise RuntimeError(f"zig build не удался: {last_err}")


class ZigBuildExt(build_ext):
    """build_ext с предварительным шагом zig build + копирование .so в _lib."""

    def run(self):
        if os.environ.get("ZG_SKIP_ZIG") == "1":
            if not _core_lib_present():
                raise RuntimeError(
                    "ZG_SKIP_ZIG=1, но в python/zig_constraints/_lib нет "
                    "libzig_constraints.so* — положите туда готовую библиотеку "
                    "или соберите без ZG_SKIP_ZIG"
                )
        else:
            _run_zig_build()
            copied = _copy_core_lib()
            print("zig_constraints: скопировано в _lib:", ", ".join(copied))
        super().run()


ext_modules = [
    Extension(
        "zig_constraints._core",
        sources=[os.path.join("zig_constraints", "_core.c")],
        include_dirs=[os.path.join(ROOT, "include")],
        library_dirs=[LIB_DIR],
        libraries=["zig_constraints"],
        runtime_library_dirs=["$ORIGIN/_lib"] if sys.platform != "darwin" else [],
        define_macros=[("Py_LIMITED_API", "0x030A0000")],
        py_limited_api=True,
    )
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": ZigBuildExt},
    options={"bdist_wheel": {"py_limited_api": "cp310"}},
)
