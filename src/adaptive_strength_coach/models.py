from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from adaptive_strength_coach.json_types import JsonObject, JsonValue


class Provider(StrEnum):
    GARMIN = "garmin"
    STRONG_IMPORT = "strong_import"
    BODYSPEC = "bodyspec"


class Sport(StrEnum):
    TENNIS = "tennis"
    CYCLING = "cycling"
    GOLF = "golf"
    WALKING = "walking"
    RUNNING = "running"
    LIFTING = "lifting"
    OTHER = "other"


class DailyRecoveryInput(BaseModel):
    date: date
    provider: Provider = Provider.GARMIN
    sleep_minutes: int | None = None
    sleep_score: float | None = None
    readiness_score: float | None = None
    hrv_ms: float | None = None
    hrv_status: str | None = None
    resting_heart_rate_bpm: float | None = None
    respiratory_rate: float | None = None
    stress_score: float | None = None
    body_battery_min: float | None = None
    body_battery_max: float | None = None
    spo2_avg: float | None = None
    wake_events: int | None = None
    source_confidence: float = Field(ge=0.0, le=1.0)


class ActivitySession(BaseModel):
    id: str
    provider: Provider = Provider.GARMIN
    started_at: datetime
    sport: Sport
    provider_sport: str | None = None
    provider_sub_sport: str | None = None
    name: str | None = None
    duration_minutes: float
    moving_minutes: float | None = None
    distance_meters: float | None = None
    average_heart_rate_bpm: float | None = None
    max_heart_rate_bpm: float | None = None
    training_load: float | None = None
    aerobic_training_effect: float | None = None
    anaerobic_training_effect: float | None = None
    calories: float | None = None
    self_eval_feel: str | None = None
    self_eval_effort: str | None = None


class RawPayload(BaseModel):
    endpoint: str
    request_key: str
    fetched_at: datetime
    payload_sha256: str
    local_path: str


class LiftingSet(BaseModel):
    id: str
    provider: Provider = Provider.STRONG_IMPORT
    started_at: datetime
    workout_name: str
    duration_minutes: int | None = None
    exercise_name: str
    set_order: int
    weight_lb: float | None = None
    reps: float | None = None
    distance: float | None = None
    seconds: float | None = None
    notes: str | None = None
    workout_notes: str | None = None
    rpe: float | None = None
    estimated_1rm_lb: float | None = None


class DexaRegion(BaseModel):
    region: str
    total_region_fat_percent: float
    total_mass_lb: float
    fat_tissue_lb: float
    lean_tissue_lb: float
    bone_mineral_content_lb: float


class DexaScan(BaseModel):
    measured_date: date
    provider: Provider = Provider.BODYSPEC
    total_body_fat_percent: float
    total_mass_lb: float
    fat_tissue_lb: float
    lean_tissue_lb: float
    bone_mineral_content_lb: float
    regions: dict[str, DexaRegion] = Field(default_factory=dict)


class StrengthEstimate(BaseModel):
    exercise_name: str
    estimated_1rm_lb: float
    weight_lb: float
    reps: float
    observed_at: datetime


class GarminDailyPayloads(BaseModel):
    day: date
    daily_summary: JsonObject | None = None
    sleep: JsonObject | None = None
    resting_hr: JsonObject | None = None
    hrv: JsonObject | None = None


GarminDailyPayloads.model_rebuild(_types_namespace={"JsonValue": JsonValue})
