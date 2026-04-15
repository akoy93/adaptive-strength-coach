from __future__ import annotations

from pathlib import Path


def app_home() -> Path:
    path = Path.home() / ".adaptive-strength-coach"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def garmin_home() -> Path:
    path = app_home() / "garmin"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def raw_garmin_home() -> Path:
    path = app_home() / "raw" / "garmin"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def sqlite_path() -> Path:
    return app_home() / "coach.sqlite3"


def garmin_session_path() -> Path:
    return garmin_home() / "session.json"
