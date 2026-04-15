from __future__ import annotations

from datetime import date

from adaptive_strength_coach.garmin import normalize_activity, normalize_daily_recovery
from adaptive_strength_coach.models import GarminDailyPayloads, Sport


def test_normalize_daily_recovery_from_garmin_payloads() -> None:
    recovery = normalize_daily_recovery(
        GarminDailyPayloads(
            day=date(2026, 4, 15),
            daily_summary={
                "averageStressLevel": 31,
                "bodyBatteryLowestValue": 42,
                "bodyBatteryHighestValue": 91,
                "rrWakingAvg": 14.2,
                "averageSpo2": 97,
            },
            sleep={
                "sleepTimeSeconds": 27_000,
                "sleepScores": {"overall": {"value": 82}},
                "awakeCount": 3,
            },
            resting_hr={
                "allMetrics": {
                    "metricsMap": {
                        "WELLNESS_RESTING_HEART_RATE": [
                            {"value": 48},
                        ],
                    },
                },
            },
            hrv={
                "lastNightAvg": 62,
                "status": "BALANCED",
            },
        )
    )

    assert recovery.sleep_minutes == 450
    assert recovery.sleep_score == 82
    assert recovery.resting_heart_rate_bpm == 48
    assert recovery.hrv_ms == 62
    assert recovery.hrv_status == "BALANCED"
    assert recovery.stress_score == 31
    assert recovery.body_battery_min == 42
    assert recovery.body_battery_max == 91
    assert recovery.source_confidence == 1.0


def test_normalize_tennis_activity_summary() -> None:
    activity = normalize_activity(
        {
            "activityId": 123,
            "activityName": "Tennis",
            "activityType": {"typeKey": "tennis", "parentTypeKey": "racquet_sports"},
            "startTimeLocal": "2026-04-15 09:30:00",
            "elapsedDuration": 5400,
            "movingDuration": 4800,
            "averageHR": 132,
            "maxHR": 181,
            "activityTrainingLoad": 78,
        },
        None,
    )

    assert activity.id == "123"
    assert activity.sport is Sport.TENNIS
    assert activity.duration_minutes == 90
    assert activity.moving_minutes == 80
    assert activity.average_heart_rate_bpm == 132
    assert activity.max_heart_rate_bpm == 181
    assert activity.training_load == 78
