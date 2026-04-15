from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from adaptive_strength_coach.garmin import GarminError, GarminSyncService
from adaptive_strength_coach.json_types import JsonObject
from adaptive_strength_coach.store import CoachStore


@dataclass(frozen=True)
class FakeGarminConfig:
    raw_dir: Path


class FakeGarminClient:
    def __init__(self, raw_dir: Path, *, detail_status: int | None = None) -> None:
        self.config = FakeGarminConfig(raw_dir=raw_dir)
        self.detail_status = detail_status

    def daily_summary(self, day: date) -> JsonObject:
        return {
            "calendarDate": day.isoformat(),
            "averageStressLevel": 20,
            "bodyBatteryLowestValue": 55,
            "bodyBatteryHighestValue": 95,
        }

    def sleep(self, day: date) -> JsonObject:
        return {
            "calendarDate": day.isoformat(),
            "sleepTimeSeconds": 28_800,
            "sleepScore": 88,
        }

    def resting_heart_rate(self, day: date) -> JsonObject:
        return {"calendarDate": day.isoformat(), "restingHeartRate": 47}

    def hrv(self, day: date) -> JsonObject:
        return {"calendarDate": day.isoformat(), "lastNightAvg": 64, "status": "BALANCED"}

    def activity_summaries(self, start: int, limit: int) -> list[JsonObject]:
        assert start == 0
        assert limit == 1
        return [
            {
                "activityId": 123,
                "activityName": "Tennis",
                "activityType": {"typeKey": "tennis", "parentTypeKey": "racquet_sports"},
                "startTimeLocal": "2026-04-15 09:30:00",
                "elapsedDuration": 5400,
                "averageHR": 132,
            }
        ]

    def activity_details(self, activity_id: str) -> JsonObject:
        assert activity_id == "123"
        if self.detail_status is not None:
            raise GarminError("detail unavailable", status_code=self.detail_status)
        return {
            "activityId": 123,
            "summaryDTO": {
                "startTimeLocal": "2026-04-15 09:30:00",
                "elapsedDuration": 5400,
                "averageHR": 133,
                "activityTrainingLoad": 78,
            },
            "activityType": {"typeKey": "tennis", "parentTypeKey": "racquet_sports"},
        }


def test_daily_sync_persists_recovery_and_private_raw_files(tmp_path: Path) -> None:
    store = CoachStore(tmp_path / "coach.sqlite3")
    client = FakeGarminClient(tmp_path / "raw")
    service = GarminSyncService(client, store)

    rows = service.sync_daily_range(date(2026, 4, 15), date(2026, 4, 15))

    assert len(rows) == 1
    assert rows[0].sleep_minutes == 480
    assert rows[0].sleep_score == 88
    assert rows[0].resting_heart_rate_bpm == 47
    assert rows[0].hrv_ms == 64
    assert store.counts()["daily_recovery_inputs"] == 1
    assert store.counts()["raw_payloads"] == 4

    raw_files = list((tmp_path / "raw").rglob("*.json"))
    assert len(raw_files) == 4
    assert all((raw_file.stat().st_mode & 0o777) == 0o600 for raw_file in raw_files)


def test_activity_sync_uses_summary_when_detail_is_missing(tmp_path: Path) -> None:
    store = CoachStore(tmp_path / "coach.sqlite3")
    client = FakeGarminClient(tmp_path / "raw", detail_status=404)
    service = GarminSyncService(client, store)

    rows = service.sync_activities(limit=1, include_details=True)

    assert len(rows) == 1
    assert rows[0].id == "123"
    assert rows[0].average_heart_rate_bpm == 132
    assert store.counts()["activity_sessions"] == 1


def test_activity_sync_propagates_rate_limits(tmp_path: Path) -> None:
    store = CoachStore(tmp_path / "coach.sqlite3")
    client = FakeGarminClient(tmp_path / "raw", detail_status=429)
    service = GarminSyncService(client, store)

    with pytest.raises(GarminError) as exc_info:
        service.sync_activities(limit=1, include_details=True)

    assert exc_info.value.status_code == 429
