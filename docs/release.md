# Build a desktop release

Use Python 3.12 and a virtual environment on the target OS. CI uses Python 3.12.10, PySide6-Essentials 6.11.3 and PyInstaller 6.20.0. The release lock pins their dependencies across all three systems. [Project requirements](../pyproject.toml) · [Release lock](../packaging/requirements-py312.lock)

From a fresh build environment in the checkout, install the build tools and package the app. On macOS, complete the wheel preparation below before running PyInstaller.

```sh
python -m pip install -c packaging/requirements-py312.lock setuptools
python -m pip install --no-build-isolation -c packaging/requirements-py312.lock . pyinstaller
python -m PyInstaller --clean --noconfirm packaging/edopal.spec
python tools/release.py package
python tools/release.py verify
```

- **Linux x86-64** needs `mksquashfs` from squashfs-tools to package the AppImage. The packager fetches the pinned static runtime and verifies its SHA-256. The smoke check extracts and runs the AppImage on hosts without a FUSE device. [Upstream runtime](https://github.com/AppImage/type2-runtime/releases/tag/20251108)
- **macOS** needs a universal2 Python build. In the build environment, use delocate 0.13.0 and run `python tools/macos_universal.py` before PyInstaller. The verifier checks the ad-hoc signature and both slices, then launches the extracted app under Apple silicon and Intel. Apple silicon builders need Rosetta for the Intel launch.
- **Windows x64** ships a ZIP. Extract the whole folder so `EDOPal.exe` can load the adjacent `_internal` directory.

Find the archives in `dist/release/`. On Linux, `./tools/try-local.sh` rebuilds and starts the onedir app. It uses `.venv/bin/python` when present, and accepts a different build interpreter through `PYTHON`. Pass `--smoke` for a one-window startup check or `--version` to print the frozen app version.

A pushed `v*` tag must match `edopal.__version__`. The release workflow waits for tests and native artifact checks on all three platforms before publishing four downloads and `SHA256SUMS`. Release candidates are published as prereleases. [Release workflow](../.github/workflows/release.yml)
