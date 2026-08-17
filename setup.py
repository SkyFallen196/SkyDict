"""py2app build configuration.

    python setup.py py2app          # release build, self-contained
    python setup.py py2app -A       # alias build, fast, runs from the source tree

The alias build is for iterating: it symlinks back to the working copy, so a code change
needs no rebuild. It is not distributable — the real build is the plain one.
"""

import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.dist import Distribution

# py2app walks the AST of every module it bundles, recursing once per node. pydantic and
# onnxruntime are deep enough to blow the default 1000-frame limit.
sys.setrecursionlimit(10_000)


class BundleDistribution(Distribution):
    """A distribution that reports no install_requires.

    setuptools fills that field from pyproject.toml's [project] dependencies, and
    py2app 0.28 aborts on any value it finds there. The bundle's contents come from
    OPTIONS["packages"] instead, so the field is simply not relevant to this build.
    """

    #: Empty rather than None: py2app treats an empty list as "nothing declared" and
    #: proceeds, while setuptools still iterates over it when writing metadata.
    @property
    def install_requires(self):
        return []

    @install_requires.setter
    def install_requires(self, value):
        pass

APP_NAME = "SkyDict"
BUNDLE_ID = "com.skydict.SkyDict"
VERSION = "0.1.0"

#: Why each of these is needed at runtime, since py2app cannot infer it:
#: onnxruntime and numpy carry native libraries; huggingface_hub is imported lazily so
#: the dependency graph never sees it; rumps and the pyobjc frameworks are reached
#: through runtime lookups; keyring loads its macOS backend by name.
PACKAGES = [
    "skydict",
    "onnx_asr",
    "onnxruntime",
    "numpy",
    "huggingface_hub",
    "httpx",
    "pydantic",
    "keyring",
    "rumps",
    "sounddevice",
    # Ships libportaudio.dylib. Listing it as a package keeps it out of the zipped
    # library — dlopen cannot read from inside a zip, so recording failed with "cannot
    # load library" the moment the hotkey was pressed.
    "_sounddevice_data",
    "platformdirs",
]

PLIST = {
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleVersion": VERSION,
    "CFBundleShortVersionString": VERSION,
    "CFBundlePackageType": "APPL",
    "NSHumanReadableCopyright": "MIT licensed",
    # Menubar app: no Dock icon, no app switcher entry. The settings window flips the
    # activation policy while it is open so it can still take keyboard focus.
    "LSUIElement": True,
    # macOS terminates the process on first microphone access if this string is absent.
    "NSMicrophoneUsageDescription": (
        "SkyDict records from the microphone while you hold the dictation hotkey, "
        "and turns what you say into text."
    ),
    # Apple Silicon and recent Intel only; nothing here supports 10.14 or earlier.
    "LSMinimumSystemVersion": "11.0",
}

CONDA_LIB = Path(sys.prefix) / "lib"


def environment_dylibs() -> list[str]:
    """Shared libraries py2app does not find on its own.

    In a conda environment the interpreter's extension modules link against libraries in
    the environment's own lib directory, either through @rpath or by absolute path.
    macholib resolves neither, so the bundle ships without them and dies on the first
    import that needs one — _ctypes on libffi, _sqlite3 on libsqlite3, and so on. Worse,
    a conda build can link against a *newer* library than the system one and fail on a
    missing symbol rather than a missing file.

    Rather than maintain a list by hand, walk the interpreter's own extension modules and
    collect whatever they reference inside this environment.
    """
    dynload = CONDA_LIB / f"python{sys.version_info.major}.{sys.version_info.minor}" / "lib-dynload"
    if not dynload.exists():
        return []

    # tcl/tk come in through _tkinter, which is excluded from the bundle anyway; they
    # would add tens of megabytes for a GUI toolkit SkyDict never loads.
    skip = ("libtcl", "libtk")

    wanted: dict[str, Path] = {}
    for module in dynload.glob("*.so"):
        try:
            output = subprocess.run(
                ["otool", "-L", str(module)], capture_output=True, text=True, check=True
            ).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

        for line in output.splitlines()[1:]:
            reference = line.strip().split(" ")[0]
            name = Path(reference).name
            if not (reference.startswith("@rpath/") or reference.startswith(str(CONDA_LIB))):
                continue
            if name.startswith(skip):
                continue
            # Keep the name the module actually asks dyld for. Following the symlink to
            # its versioned target would install the library under a name nothing looks
            # up — libsqlite3.0.dylib is a link to libsqlite3.3.53.2.dylib, and only the
            # former is ever requested.
            candidate = CONDA_LIB / name
            if candidate.exists():
                wanted[name] = candidate

    return sorted(str(path) for path in wanted.values())


FRAMEWORKS = environment_dylibs()

OPTIONS = {
    "packages": PACKAGES,
    "plist": PLIST,
    "iconfile": "assets/SkyDict.icns",
    "frameworks": FRAMEWORKS,
    # The tests and the build tooling have no business inside the bundle.
    "excludes": ["pytest", "py2app", "setuptools", "tkinter", "test", "tests"],
    "arch": "arm64",
}

# py2app 0.28 cannot sign, so `make sign` (see build.sh) runs codesign afterwards. The
# signature matters: without a stable identity macOS forgets the Accessibility grant on
# every rebuild and the hotkey silently stops working.

setup(
    name=APP_NAME,
    version=VERSION,
    app=["skydict_app.py"],
    options={"py2app": OPTIONS},
    distclass=BundleDistribution,
)
