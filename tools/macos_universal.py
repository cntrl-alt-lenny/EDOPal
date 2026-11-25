"""Replace single-architecture wheels in the macOS build venv with dual-architecture wheels."""

import subprocess
import sys
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory


def thin_wheel_requirements() -> list[str]:
    requirements = []
    for dist in metadata.distributions():
        wheel = dist.read_text("WHEEL") or ""
        tags = [line for line in wheel.splitlines() if line.startswith("Tag:")]
        if any("_universal2" in tag for tag in tags):
            continue
        if any("macosx_" in tag for tag in tags):
            requirements.append(f"{dist.metadata['Name']}=={dist.version}")
    return sorted(requirements)


def prepare() -> None:
    from delocate.fuse import fuse_wheels

    requirements = thin_wheel_requirements()
    if not requirements:
        return
    pip = [sys.executable, "-m", "pip"]
    with TemporaryDirectory() as temp:
        root = Path(temp)
        pairs = []
        for arch in ("arm64", "x86_64"):
            folder = root / arch
            subprocess.run(
                [
                    *pip,
                    "download",
                    "--no-deps",
                    "--only-binary=:all:",
                    "--platform",
                    f"macosx_15_0_{arch}",
                    "--dest",
                    str(folder),
                    *requirements,
                ],
                check=True,
            )
            pairs.append({path.name.split("-", 1)[0]: path for path in folder.glob("*.whl")})

        arm, intel = pairs
        if arm.keys() != intel.keys() or len(arm) != len(requirements):
            raise ValueError("Missing matching arm64 and x86_64 wheels")
        merged = root / "merged"
        merged.mkdir()
        wheels = []
        for project, arm_wheel in arm.items():
            candidates = (arm_wheel, intel[project])
            both = next((path for path in candidates if "_universal2" in path.name), None)
            wheels.append(both or fuse_wheels(*candidates, merged))
        subprocess.run([*pip, "install", "--no-index", "--no-deps", "--force-reinstall", *wheels], check=True)


if __name__ == "__main__":
    prepare()
