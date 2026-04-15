from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from garth import Client as GarthClient
from garth.exc import GarthException, GarthHTTPError
from requests import HTTPError as RequestsHTTPError

from adaptive_strength_coach.json_types import JsonObject, JsonValue
from adaptive_strength_coach.models import (
    ActivitySession,
    DailyRecoveryInput,
    GarminDailyPayloads,
    RawPayload,
    Sport,
)
from adaptive_strength_coach.paths import garmin_session_path, raw_garmin_home
from adaptive_strength_coach.security import (
    SecretError,
    read_keychain_internet_password,
    write_private_bytes,
    write_private_text,
)
from adaptive_strength_coach.store import CoachStore


class GarminError(RuntimeError):
    """Raised when Garmin read-only sync fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GarminMfaRequiredError(GarminError):
    """Raised when Garmin asks for MFA and no MFA callback was supplied."""


@dataclass(frozen=True)
class GarminConfig:
    account: str
    domain: str = "garmin.com"
    keychain_server: str = "sso.garmin.com"
    session_path: Path = garmin_session_path()
    raw_dir: Path = raw_garmin_home()
    rate_limit_seconds: float = 1.0
    recent_overlap_days: int = 3


class GarminClient:
    DAILY_SUMMARY_PATH = "/usersummary-service/usersummary/daily"
    SLEEP_PATH = "/wellness-service/wellness/dailySleepData"
    RHR_PATH = "/userstats-service/wellness/daily"
    HRV_PATH = "/hrv-service/hrv"
    ACTIVITY_SEARCH_PATH = "/activitylist-service/activities/search/activities"
    ACTIVITY_DETAIL_PATH = "/activity-service/activity"
    ACTIVITY_TYPES_PATH = "/activity-service/activity/activityTypes"

    def __init__(self, config: GarminConfig) -> None:
        self.config = config
        self.client = GarthClient()
        self.client.configure(domain=config.domain)
        self._profile_cache: JsonObject | None = None

    def load_session(self) -> bool:
        if not self.config.session_path.exists():
            return False
        try:
            self.client.loads(self.config.session_path.read_text(encoding="utf-8"))
        except (GarthException, ValueError, TypeError) as exc:
            raise GarminError(
                f"Saved Garmin session at {self.config.session_path} could not be loaded."
            ) from exc
        return True

    def save_session(self) -> None:
        write_private_text(self.config.session_path, self.client.dumps())

    def login_from_keychain(self, mfa_callback: Callable[[], str] | None = None) -> None:
        try:
            password = read_keychain_internet_password(self.config.keychain_server, self.config.account)
        except SecretError as exc:
            raise GarminError(str(exc)) from exc
        try:
            if mfa_callback is None:
                oauth1, _ = self.client.login(
                    self.config.account,
                    password,
                    prompt_mfa=None,
                    return_on_mfa=True,
                )
                if oauth1 == "needs_mfa":
                    raise GarminMfaRequiredError("Garmin requested MFA. Re-run auth with `--mfa-code`.")
            else:
                self.client.login(self.config.account, password, prompt_mfa=mfa_callback)
        except GarthHTTPError as exc:
            raise self._garth_error("Garmin login failed", exc) from exc
        except RequestsHTTPError as exc:
            raise self._requests_error("Garmin login failed", exc) from exc
        except GarthException as exc:
            raise GarminError(f"Garmin login failed: {exc}") from exc
        self.save_session()
        self._profile_cache = None

    def ensure_session(self, allow_login: bool) -> None:
        loaded = False
        try:
            loaded = self.load_session()
        except GarminError:
            if not allow_login:
                raise
        if loaded and self.is_session_valid():
            return
        if not allow_login:
            raise GarminError("No valid Garmin session. Run `coach garmin auth` first.")
        self.login_from_keychain()

    def is_session_valid(self) -> bool:
        try:
            _ = self.profile()
            return True
        except GarminError:
            return False

    def profile(self) -> JsonObject:
        if self._profile_cache is not None:
            return self._profile_cache
        try:
            profile = self.client.profile
        except GarthHTTPError as exc:
            raise self._garth_error("Failed to read Garmin profile", exc) from exc
        except RequestsHTTPError as exc:
            raise self._requests_error("Failed to read Garmin profile", exc) from exc
        if not isinstance(profile, dict):
            raise GarminError("Garmin profile response was not an object.")
        self._profile_cache = _json_object(profile)
        return self._profile_cache

    def display_name(self) -> str:
        profile = self.profile()
        display_name = profile.get("displayName")
        if not isinstance(display_name, str) or display_name == "":
            raise GarminError("Garmin profile did not include a displayName.")
        return display_name

    def daily_summary(self, day: date) -> JsonObject:
        display_name = self.display_name()
        params: JsonObject = {
            "calendarDate": day.isoformat(),
        }
        data = self._connectapi_json(f"{self.DAILY_SUMMARY_PATH}/{display_name}", params=params, missing_ok=True)
        return data

    def sleep(self, day: date) -> JsonObject:
        display_name = self.display_name()
        params: JsonObject = {
            "date": day.isoformat(),
            "nonSleepBufferMinutes": 60,
        }
        return self._connectapi_json(f"{self.SLEEP_PATH}/{display_name}", params=params, missing_ok=True)

    def resting_heart_rate(self, day: date) -> JsonObject:
        display_name = self.display_name()
        params: JsonObject = {
            "fromDate": day.isoformat(),
            "untilDate": day.isoformat(),
            "metricId": 60,
        }
        return self._connectapi_json(f"{self.RHR_PATH}/{display_name}", params=params, missing_ok=True)

    def hrv(self, day: date) -> JsonObject:
        return self._connectapi_json(f"{self.HRV_PATH}/{day.isoformat()}", missing_ok=True)

    def activity_summaries(self, start: int, limit: int) -> list[JsonObject]:
        params: JsonObject = {"start": start, "limit": limit}
        data = self._connectapi_value(self.ACTIVITY_SEARCH_PATH, params=params)
        if not isinstance(data, list):
            raise GarminError("Garmin activity search response was not a list.")
        return [_json_object(item) for item in data if isinstance(item, dict)]

    def activity_details(self, activity_id: str) -> JsonObject:
        return self._connectapi_json(f"{self.ACTIVITY_DETAIL_PATH}/{activity_id}")

    def _connectapi_value(self, path: str, params: JsonObject | None = None, *, missing_ok: bool = False) -> JsonValue:
        try:
            time.sleep(self.config.rate_limit_seconds)
            value = self.client.connectapi(path, params=params)
        except GarthHTTPError as exc:
            status_code = self._garth_status_code(exc)
            if missing_ok and status_code == 404:
                return None
            raise self._garth_error(f"Garmin GET failed for {path}", exc) from exc
        except RequestsHTTPError as exc:
            status_code = self._requests_status_code(exc)
            if missing_ok and status_code == 404:
                return None
            raise self._requests_error(f"Garmin GET failed for {path}", exc) from exc
        if not _is_json_value(value):
            raise GarminError(f"Garmin response for {path} was not JSON-compatible.")
        return value

    def _connectapi_json(self, path: str, params: JsonObject | None = None, *, missing_ok: bool = False) -> JsonObject:
        value = self._connectapi_value(path, params=params, missing_ok=missing_ok)
        if value is None and missing_ok:
            return {}
        if not isinstance(value, dict):
            raise GarminError(f"Garmin response for {path} was not an object.")
        return _json_object(value)

    @staticmethod
    def _garth_status_code(exc: GarthHTTPError) -> int | None:
        response = exc.error.response
        return response.status_code if response is not None else None

    @staticmethod
    def _requests_status_code(exc: RequestsHTTPError) -> int | None:
        response = exc.response
        return response.status_code if response is not None else None

    @classmethod
    def _garth_error(cls, prefix: str, exc: GarthHTTPError) -> GarminError:
        status_code = cls._garth_status_code(exc)
        return GarminError(cls._http_error_message(prefix, status_code), status_code=status_code)

    @classmethod
    def _requests_error(cls, prefix: str, exc: RequestsHTTPError) -> GarminError:
        status_code = cls._requests_status_code(exc)
        return GarminError(cls._http_error_message(prefix, status_code), status_code=status_code)

    @staticmethod
    def _http_error_message(prefix: str, status_code: int | None) -> str:
        if status_code == 429:
            return f"{prefix}: Garmin returned 429 Too Many Requests. Wait before retrying; no data was modified."
        status_text = str(status_code) if status_code is not None else "unknown"
        return f"{prefix}: HTTP {status_text}. No data was modified."


class GarminSyncService:
    def __init__(self, client: GarminClient, store: CoachStore) -> None:
        self.client = client
        self.store = store

    def sync_daily_range(self, start_day: date, end_day: date) -> list[DailyRecoveryInput]:
        recoveries: list[DailyRecoveryInput] = []
        for day in _date_range(start_day, end_day):
            payloads = GarminDailyPayloads(
                day=day,
                daily_summary=self._fetch_and_store_json("daily_summary", day.isoformat(), self.client.daily_summary, day),
                sleep=self._fetch_and_store_json("sleep", day.isoformat(), self.client.sleep, day),
                resting_hr=self._fetch_and_store_json("resting_hr", day.isoformat(), self.client.resting_heart_rate, day),
                hrv=self._fetch_and_store_json("hrv", day.isoformat(), self.client.hrv, day),
            )
            recovery = normalize_daily_recovery(payloads)
            self.store.upsert_daily_recovery(recovery)
            recoveries.append(recovery)
        return recoveries

    def sync_activities(self, limit: int, include_details: bool) -> list[ActivitySession]:
        raw_summaries = self.client.activity_summaries(start=0, limit=limit)
        self._store_raw_value("activity_search", f"start=0-limit={limit}", raw_summaries)
        activities: list[ActivitySession] = []
        for summary in raw_summaries:
            activity_id_value = summary.get("activityId")
            if activity_id_value is None:
                continue
            activity_id = str(activity_id_value)
            details: JsonObject | None = None
            if include_details:
                try:
                    details = self._fetch_and_store_json("activity_details", activity_id, self.client.activity_details, activity_id)
                except GarminError as exc:
                    if exc.status_code != 404:
                        raise
            activity = normalize_activity(summary, details)
            self.store.upsert_activity(activity)
            activities.append(activity)
        return activities

    def _fetch_and_store_json(
        self,
        endpoint: str,
        request_key: str,
        fetcher: Callable[..., JsonObject],
        *args: object,
    ) -> JsonObject:
        payload = fetcher(*args)
        self._store_raw_value(endpoint, request_key, payload)
        return payload

    def _store_raw_value(self, endpoint: str, request_key: str, value: JsonValue) -> RawPayload:
        fetched_at = datetime.now()
        raw_bytes = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sha = hashlib.sha256(raw_bytes).hexdigest()
        safe_endpoint = endpoint.replace("/", "_")
        safe_request = request_key.replace("/", "_").replace(":", "_")
        output_dir = self.client.config.raw_dir / safe_endpoint
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        output_path = output_dir / f"{safe_request}-{sha[:12]}.json"
        write_private_bytes(output_path, raw_bytes)
        payload = RawPayload(
            endpoint=endpoint,
            request_key=request_key,
            fetched_at=fetched_at,
            payload_sha256=sha,
            local_path=str(output_path),
        )
        self.store.upsert_raw_payload(payload)
        return payload


def normalize_daily_recovery(payloads: GarminDailyPayloads) -> DailyRecoveryInput:
    daily = payloads.daily_summary or {}
    sleep = payloads.sleep or {}
    rhr = payloads.resting_hr or {}
    hrv = payloads.hrv or {}

    sleep_minutes = _first_int(
        sleep,
        [
            "sleepTimeSeconds",
            "totalSleepSeconds",
        ],
        divide_by=60,
    )
    sleep_score = _nested_float(sleep, ["sleepScores", "overall", "value"])
    if sleep_score is None:
        sleep_score = _first_float(sleep, ["sleepScore", "overallSleepScore"])

    resting_hr = _extract_rhr(rhr) or _first_float(daily, ["restingHeartRate", "rhr"])
    hrv_ms = _first_float(hrv, ["lastNightAvg", "lastNightAverage", "lastNightAvgValue"])
    hrv_status_value = hrv.get("status")
    hrv_status = hrv_status_value if isinstance(hrv_status_value, str) else None

    return DailyRecoveryInput(
        date=payloads.day,
        sleep_minutes=sleep_minutes,
        sleep_score=sleep_score,
        hrv_ms=hrv_ms,
        hrv_status=hrv_status,
        resting_heart_rate_bpm=resting_hr,
        respiratory_rate=_first_float(daily, ["rrWakingAvg", "averageRespiration", "avgRespiration"]),
        stress_score=_first_float(daily, ["averageStressLevel", "stressAvg", "stressLevel"]),
        body_battery_min=_first_float(daily, ["bodyBatteryLowestValue", "bbMin", "bodyBatteryMin"]),
        body_battery_max=_first_float(daily, ["bodyBatteryHighestValue", "bbMax", "bodyBatteryMax"]),
        spo2_avg=_first_float(daily, ["averageSpo2", "spo2Avg"]),
        wake_events=_first_int(sleep, ["awakeCount", "numberOfAwakenings"]),
        source_confidence=_daily_confidence(daily, sleep, rhr, hrv),
    )


def normalize_activity(summary: JsonObject, details: JsonObject | None) -> ActivitySession:
    source = details if details is not None else summary
    summary_dto = source.get("summaryDTO") if isinstance(source.get("summaryDTO"), dict) else source
    metadata_dto = source.get("metadataDTO") if isinstance(source.get("metadataDTO"), dict) else {}
    summary_obj = _json_object(summary_dto) if isinstance(summary_dto, dict) else summary
    metadata_obj = _json_object(metadata_dto) if isinstance(metadata_dto, dict) else {}

    activity_id_value = summary.get("activityId") or source.get("activityId")
    if activity_id_value is None:
        raise GarminError("Activity was missing activityId.")

    provider_sport = _activity_type_key(summary, source, "typeKey")
    provider_sub_sport = _activity_type_key(summary, source, "parentTypeKey")
    started_at = _parse_garmin_datetime(_first_str(summary_obj, ["startTimeLocal", "startTimeGMT"]))
    elapsed_seconds = _first_float(summary_obj, ["elapsedDuration", "duration"]) or 0.0

    feel = _garmin_feel_label(_first_float(summary_obj, ["directWorkoutFeel"]))
    effort = _garmin_effort_label(_first_float(summary_obj, ["directWorkoutRpe"]))

    return ActivitySession(
        id=str(activity_id_value),
        started_at=started_at,
        sport=_map_sport(provider_sport, provider_sub_sport, summary.get("activityName")),
        provider_sport=provider_sport,
        provider_sub_sport=provider_sub_sport,
        name=_first_str(summary, ["activityName"]) or _first_str(source, ["activityName"]),
        duration_minutes=elapsed_seconds / 60.0,
        moving_minutes=_optional_seconds_to_minutes(_first_float(summary_obj, ["movingDuration"])),
        distance_meters=_first_float(summary_obj, ["distance"]),
        average_heart_rate_bpm=_first_float(summary_obj, ["averageHR", "averageHeartRate"]),
        max_heart_rate_bpm=_first_float(summary_obj, ["maxHR", "maxHeartRate"]),
        training_load=_first_float(summary_obj, ["activityTrainingLoad", "trainingLoad"]),
        aerobic_training_effect=_first_float(summary_obj, ["aerobicTrainingEffect"]),
        anaerobic_training_effect=_first_float(summary_obj, ["anaerobicTrainingEffect"]),
        calories=_first_float(summary_obj, ["calories"]),
        self_eval_feel=feel,
        self_eval_effort=effort,
    )


def _date_range(start_day: date, end_day: date) -> list[date]:
    if end_day < start_day:
        raise GarminError("End date must be on or after start date.")
    days = (end_day - start_day).days
    return [start_day + timedelta(days=offset) for offset in range(days + 1)]


def _extract_rhr(payload: JsonObject) -> float | None:
    value = payload.get("allMetrics")
    if isinstance(value, dict):
        metrics = value.get("metricsMap")
        if isinstance(metrics, dict):
            wellness = metrics.get("WELLNESS_RESTING_HEART_RATE")
            if isinstance(wellness, list) and wellness:
                first = wellness[0]
                if isinstance(first, dict):
                    return _first_float(_json_object(first), ["value"])
    return _first_float(payload, ["restingHeartRate", "value"])


def _daily_confidence(daily: JsonObject, sleep: JsonObject, rhr: JsonObject, hrv: JsonObject) -> float:
    present = sum(1 for payload in [daily, sleep, rhr, hrv] if len(payload) > 0)
    return min(1.0, 0.25 * present)


def _first_float(payload: JsonObject, keys: list[str]) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def _first_int(payload: JsonObject, keys: list[str], divide_by: int | None = None) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int | float):
            result = int(value)
            return int(result / divide_by) if divide_by is not None else result
    return None


def _first_str(payload: JsonObject, keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value != "":
            return value
    return None


def _nested_float(payload: JsonObject, keys: list[str]) -> float | None:
    current: JsonValue = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, int | float):
        return float(current)
    return None


def _activity_type_key(summary: JsonObject, source: JsonObject, key: str) -> str | None:
    for payload in [summary, source]:
        activity_type = payload.get("activityType")
        if isinstance(activity_type, dict):
            value = activity_type.get(key)
            if isinstance(value, str):
                return value
    return None


def _map_sport(provider_sport: str | None, provider_sub_sport: str | None, name: JsonValue) -> Sport:
    values = " ".join(value.lower() for value in [provider_sport, provider_sub_sport, name] if isinstance(value, str))
    if "tennis" in values or "racquet" in values:
        return Sport.TENNIS
    if "cycling" in values or "biking" in values or "bike" in values:
        return Sport.CYCLING
    if "golf" in values:
        return Sport.GOLF
    if "walking" in values or "hiking" in values:
        return Sport.WALKING
    if "running" in values:
        return Sport.RUNNING
    if "strength" in values or "training" in values:
        return Sport.LIFTING
    return Sport.OTHER


def _parse_garmin_datetime(value: str | None) -> datetime:
    if value is None:
        raise GarminError("Activity was missing startTimeLocal/startTimeGMT.")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _optional_seconds_to_minutes(value: float | None) -> float | None:
    return value / 60.0 if value is not None else None


def _garmin_feel_label(value: float | None) -> str | None:
    if value is None:
        return None
    levels = [(100, "Very Strong"), (75, "Strong"), (50, "Normal"), (25, "Weak"), (0, "Very Weak")]
    for threshold, label in levels:
        if value >= threshold:
            return label
    return None


def _garmin_effort_label(value: float | None) -> str | None:
    if value is None:
        return None
    levels = [
        (100, "Maximum"),
        (90, "Extremely Hard"),
        (70, "Very Hard"),
        (50, "Hard"),
        (40, "Somewhat Hard"),
        (30, "Moderate"),
        (20, "Light"),
        (10, "Very Light"),
        (0, "None"),
    ]
    for threshold, label in levels:
        if value >= threshold:
            return label
    return None


def _json_object(value: dict[object, object]) -> JsonObject:
    result: JsonObject = {}
    for key, item in value.items():
        if isinstance(key, str) and _is_json_value(item):
            result[key] = item
    return result


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, bool | int | float | str):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False
