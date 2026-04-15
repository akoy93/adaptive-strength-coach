from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from adaptive_strength_coach.models import (
    ActivitySession,
    DailyRecoveryInput,
    DexaScan,
    LiftingSet,
    RawPayload,
    StrengthEstimate,
)


class CoachStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists raw_payloads (
                    endpoint text not null,
                    request_key text not null,
                    fetched_at text not null,
                    payload_sha256 text not null,
                    local_path text not null,
                    primary key (endpoint, request_key, payload_sha256)
                );

                create table if not exists daily_recovery_inputs (
                    day text not null,
                    provider text not null,
                    payload_json text not null,
                    updated_at text not null,
                    primary key (day, provider)
                );

                create table if not exists activity_sessions (
                    id text primary key,
                    provider text not null,
                    payload_json text not null,
                    started_at text not null,
                    sport text not null,
                    updated_at text not null
                );

                create table if not exists lifting_sets (
                    id text primary key,
                    provider text not null,
                    payload_json text not null,
                    started_at text not null,
                    exercise_name text not null,
                    weight_lb real,
                    reps real,
                    estimated_1rm_lb real,
                    updated_at text not null
                );

                create table if not exists dexa_scans (
                    measured_date text primary key,
                    provider text not null,
                    payload_json text not null,
                    updated_at text not null
                );
                """
            )

    def upsert_raw_payload(self, payload: RawPayload) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert or ignore into raw_payloads (
                    endpoint, request_key, fetched_at, payload_sha256, local_path
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    payload.endpoint,
                    payload.request_key,
                    payload.fetched_at.isoformat(),
                    payload.payload_sha256,
                    payload.local_path,
                ),
            )

    def upsert_daily_recovery(self, recovery: DailyRecoveryInput) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into daily_recovery_inputs (day, provider, payload_json, updated_at)
                values (?, ?, ?, ?)
                on conflict(day, provider) do update set
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    recovery.date.isoformat(),
                    recovery.provider.value,
                    recovery.model_dump_json(),
                    now,
                ),
            )

    def upsert_activity(self, activity: ActivitySession) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                insert into activity_sessions (id, provider, payload_json, started_at, sport, updated_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    payload_json=excluded.payload_json,
                    started_at=excluded.started_at,
                    sport=excluded.sport,
                    updated_at=excluded.updated_at
                """,
                (
                    activity.id,
                    activity.provider.value,
                    activity.model_dump_json(),
                    activity.started_at.isoformat(),
                    activity.sport.value,
                    now,
                ),
            )

    def upsert_lifting_set(self, lifting_set: LiftingSet) -> None:
        self.upsert_lifting_sets([lifting_set])

    def upsert_lifting_sets(self, lifting_sets: list[LiftingSet]) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                insert into lifting_sets (
                    id, provider, payload_json, started_at, exercise_name,
                    weight_lb, reps, estimated_1rm_lb, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    payload_json=excluded.payload_json,
                    started_at=excluded.started_at,
                    exercise_name=excluded.exercise_name,
                    weight_lb=excluded.weight_lb,
                    reps=excluded.reps,
                    estimated_1rm_lb=excluded.estimated_1rm_lb,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        lifting_set.id,
                        lifting_set.provider.value,
                        lifting_set.model_dump_json(),
                        lifting_set.started_at.isoformat(),
                        lifting_set.exercise_name,
                        lifting_set.weight_lb,
                        lifting_set.reps,
                        lifting_set.estimated_1rm_lb,
                        now,
                    )
                    for lifting_set in lifting_sets
                ],
            )

    def upsert_dexa_scan(self, scan: DexaScan) -> None:
        self.upsert_dexa_scans([scan])

    def upsert_dexa_scans(self, scans: list[DexaScan]) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                insert into dexa_scans (measured_date, provider, payload_json, updated_at)
                values (?, ?, ?, ?)
                on conflict(measured_date) do update set
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        scan.measured_date.isoformat(),
                        scan.provider.value,
                        scan.model_dump_json(),
                        now,
                    )
                    for scan in scans
                ],
            )

    def latest_daily_recovery(self, limit: int) -> list[DailyRecoveryInput]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json from daily_recovery_inputs
                order by day desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [DailyRecoveryInput.model_validate_json(row["payload_json"]) for row in rows]

    def latest_activities(self, limit: int) -> list[ActivitySession]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json from activity_sessions
                order by started_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [ActivitySession.model_validate_json(row["payload_json"]) for row in rows]

    def latest_lifting_sets(self, limit: int) -> list[LiftingSet]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json from lifting_sets
                order by started_at desc, exercise_name, id
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [LiftingSet.model_validate_json(row["payload_json"]) for row in rows]

    def all_lifting_sets(self) -> list[LiftingSet]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json from lifting_sets
                order by started_at desc, exercise_name, id
                """
            ).fetchall()
        return [LiftingSet.model_validate_json(row["payload_json"]) for row in rows]

    def latest_dexa_scans(self, limit: int) -> list[DexaScan]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json from dexa_scans
                order by measured_date desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [DexaScan.model_validate_json(row["payload_json"]) for row in rows]

    def top_strength_estimates(self, limit: int) -> list[StrengthEstimate]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json from lifting_sets
                where estimated_1rm_lb is not null
                """,
            ).fetchall()
        best_by_exercise: dict[str, StrengthEstimate] = {}
        for row in rows:
            lifting_set = LiftingSet.model_validate_json(row["payload_json"])
            if (
                lifting_set.estimated_1rm_lb is not None
                and lifting_set.weight_lb is not None
                and lifting_set.reps is not None
            ):
                estimate = StrengthEstimate(
                    exercise_name=lifting_set.exercise_name,
                    estimated_1rm_lb=lifting_set.estimated_1rm_lb,
                    weight_lb=lifting_set.weight_lb,
                    reps=lifting_set.reps,
                    observed_at=lifting_set.started_at,
                )
                existing = best_by_exercise.get(estimate.exercise_name)
                if existing is None or estimate.estimated_1rm_lb > existing.estimated_1rm_lb:
                    best_by_exercise[estimate.exercise_name] = estimate
        estimates = sorted(
            best_by_exercise.values(),
            key=lambda estimate: estimate.estimated_1rm_lb,
            reverse=True,
        )
        return estimates[:limit]

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            result: dict[str, int] = {}
            for table in [
                "raw_payloads",
                "daily_recovery_inputs",
                "activity_sessions",
                "lifting_sets",
                "dexa_scans",
            ]:
                value = conn.execute(f"select count(*) as count from {table}").fetchone()
                result[table] = int(value["count"])
            return result

    def rows_for_day(self, day: date) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select provider, payload_json from daily_recovery_inputs where day = ?
                """,
                (day.isoformat(),),
            ).fetchall()
        return [{"provider": row["provider"], "payload_json": row["payload_json"]} for row in rows]

    @staticmethod
    def pretty_json(value: object) -> str:
        return json.dumps(value, indent=2, sort_keys=True)
