from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path

from adaptive_strength_coach.models import LiftingSet


class StrongImportError(RuntimeError):
    """Raised when a Strong CSV cannot be imported."""


_DURATION_RE = re.compile(r"(?:(?P<hours>\d+)h)?\s*(?:(?P<minutes>\d+)m)?\s*(?:(?P<seconds>\d+)s)?")


def import_strong_csv(path: Path) -> list[LiftingSet]:
    if not path.exists():
        raise StrongImportError(f"Strong CSV not found: {path}")

    rows: list[LiftingSet] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row_number, row in enumerate(reader, start=2):
            lifting_set = _parse_row(row, row_number)
            if lifting_set is not None:
                rows.append(lifting_set)
    return rows


def _parse_row(row: dict[str, str], row_number: int) -> LiftingSet | None:
    started_at = _parse_datetime(_required(row, "Date"), row_number)
    workout_name = _blank_to_none(row.get("Workout Name")) or "Unnamed Workout"
    exercise_name = _required(row, "Exercise Name")
    set_order = _parse_set_order(row.get("Set Order"))
    if set_order is None:
        return None
    weight = _parse_optional_float(row.get("Weight"), row_number, "Weight")
    reps = _parse_optional_float(row.get("Reps"), row_number, "Reps")
    distance = _parse_optional_float(row.get("Distance"), row_number, "Distance")
    seconds = _parse_optional_float(row.get("Seconds"), row_number, "Seconds")
    rpe = _parse_optional_float(row.get("RPE"), row_number, "RPE")
    duration_minutes = _parse_duration_minutes(row.get("Duration"))
    estimated_1rm = _estimated_1rm(weight, reps)

    return LiftingSet(
        id=_set_id(started_at, workout_name, exercise_name, set_order, row_number),
        started_at=started_at,
        workout_name=workout_name,
        duration_minutes=duration_minutes,
        exercise_name=exercise_name,
        set_order=set_order,
        weight_lb=weight,
        reps=reps,
        distance=distance,
        seconds=seconds,
        notes=_blank_to_none(row.get("Notes")),
        workout_notes=_blank_to_none(row.get("Workout Notes")),
        rpe=rpe,
        estimated_1rm_lb=estimated_1rm,
    )


def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key)
    if value is None or value == "":
        raise StrongImportError(f"Strong CSV row is missing {key}.")
    return value


def _parse_datetime(value: str, row_number: int) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise StrongImportError(f"Invalid Date on row {row_number}: {value}") from exc


def _parse_set_order(value: str | None) -> int | None:
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    try:
        return int(float(normalized))
    except ValueError:
        return None


def _parse_optional_float(value: str | None, row_number: int, field: str) -> float | None:
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    try:
        parsed = float(normalized)
    except ValueError as exc:
        raise StrongImportError(f"Invalid {field} on row {row_number}: {normalized}") from exc
    return parsed if parsed > 0 else None


def _parse_duration_minutes(value: str | None) -> int | None:
    normalized = _blank_to_none(value)
    if normalized is None:
        return None
    match = _DURATION_RE.fullmatch(normalized.strip())
    if match is None:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total_minutes = hours * 60 + minutes + round(seconds / 60)
    return total_minutes if total_minutes > 0 else None


def _estimated_1rm(weight: float | None, reps: float | None) -> float | None:
    if weight is None or reps is None:
        return None
    if reps < 1 or reps > 12:
        return None
    return round(weight * (1 + reps / 30), 1)


def _set_id(started_at: datetime, workout_name: str, exercise_name: str, set_order: int, row_number: int) -> str:
    raw_id = "|".join([started_at.isoformat(), workout_name, exercise_name, str(set_order), str(row_number)])
    digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
    return f"strong:{digest}"


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped != "" else None
