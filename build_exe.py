"""
Build Windows folder: dist/WhatelseCheckin/

Usage (from checkin-automation):
  python build_exe.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_NAME = "WhatelseCheckin"


def main() -> None:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "pyinstaller", "tzdata", "python-dotenv", "-q"]
    )

    work = ROOT / "build"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--console",
        "--name",
        DIST_NAME,
        "--onedir",
        "--collect-all",
        "tzdata",
        "--hidden-import",
        "paths",
        "--hidden-import",
        "config",
        "--hidden-import",
        "store",
        "--hidden-import",
        "fields",
        "--hidden-import",
        "server",
        "--hidden-import",
        "siba",
        "--hidden-import",
        "ine",
        "--hidden-import",
        "mrz",
        "--hidden-import",
        "links",
        "--hidden-import",
        "link_mail",
        "--hidden-import",
        "dotenv",
        str(ROOT / "run.py"),
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    out = ROOT / "dist" / DIST_NAME
    env_src = ROOT / ".env"
    if env_src.exists():
        shutil.copy2(env_src, out / ".env")
        print("Copied .env next to exe")
    else:
        print("WARNING: .env was not found. Copy .env next to the exe on the hotel PC.")

    example = ROOT / ".env.example"
    if example.exists():
        shutil.copy2(example, out / ".env.example")

    (out / "README_START_HERE.txt").write_text(
        """WHATELSE — check-in (SIBA / INE)
================================

HOW TO START
1. Keep this whole folder together (do not move only the .exe).
2. Keep the .env file in this folder (SIBA + INE login). Do not share it.
3. Double-click: WhatelseCheckin.exe
4. The browser opens to http://127.0.0.1:8787/checkin
5. Leave the console window open. Press Ctrl+C to stop.

WHAT IT DOES
- Staff check-in form (optional MRZ paste; given name + check-out required)
- Guest check-in links at /links (guest fills; foreign guests auto-SIBA)
- Target: email link 15 days before check-in (needs public URL + Resend)
- Save guests in data/checkins.json
- Monthly INE IPHH Quadro 1 at /ine (copy into WebInq)

LINKS (while the exe is running)
- Form:  http://127.0.0.1:8787/checkin
- List:  http://127.0.0.1:8787/list
- Links: http://127.0.0.1:8787/links
- INE:   http://127.0.0.1:8787/ine

IMPORTANT
- Internet required for SIBA send.
- Portuguese guests (PRT) are not sent to SIBA.
- INE is not auto-sent; use /ine then WebInq by day 10.
- Do not commit or email the .env file.
""",
        encoding="utf-8",
    )

    print()
    print("DONE. Folder:")
    print(" ", out)


if __name__ == "__main__":
    main()
