from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from adaptive_strength_coach.dexa import DexaImportError, import_bodyspec_path
from adaptive_strength_coach.garmin import GarminClient, GarminConfig, GarminError, GarminSyncService
from adaptive_strength_coach.models import DexaScan, LiftingSet
from adaptive_strength_coach.paths import garmin_session_path, sqlite_path
from adaptive_strength_coach.security import SecretError, read_keychain_internet_password
from adaptive_strength_coach.store import CoachStore
from adaptive_strength_coach.strong import StrongImportError, import_strong_csv

app = typer.Typer(help="Adaptive Strength Coach CLI.")
garmin_app = typer.Typer(help="Read-only Garmin Connect sync commands.")
import_app = typer.Typer(help="Bootstrap historical health and lifting data.")
inspect_app = typer.Typer(help="Inspect normalized training state.")
app.add_typer(garmin_app, name="garmin")
app.add_typer(import_app, name="import")
app.add_typer(inspect_app, name="inspect")
console = Console()

_KEY_LIFT_ALIASES: dict[str, list[str]] = {
    "Squat": ["Squat (Barbell)", "Front Squat", "Safety Squat"],
    "Bench": ["Bench Press (Barbell)"],
    "Overhead Press": ["Overhead Press (Barbell)"],
    "Row": ["Bent Over Row (Barbell)", "Pendlay Row (Barbell)", "Seated Row (Cable)", "Seated Row (Machine)"],
    "Romanian Deadlift": ["Romanian Deadlift (Barbell)"],
    "Trap Bar Deadlift": ["Trap Bar Deadlift", "Trap Bar Deadlift (Level)"],
    "Pull Up": ["Pull Up"],
}


def _garmin_client(account: str) -> GarminClient:
    config = GarminConfig(account=account)
    return GarminClient(config)


def _store() -> CoachStore:
    return CoachStore(sqlite_path())


@garmin_app.command("auth")
def garmin_auth(
    account: Annotated[str, typer.Option(help="Garmin account email.")],
    mfa_code: Annotated[str | None, typer.Option(help="Garmin MFA code, if Garmin asks for one.")] = None,
) -> None:
    """Create or refresh a local read-only Garmin session from macOS Keychain credentials."""
    client = _garmin_client(account)
    try:
        client.login_from_keychain(mfa_callback=_mfa_callback(mfa_code))
        profile = client.profile()
    except GarminError as exc:
        raise typer.Exit(_print_error(exc)) from exc
    display_name = profile.get("displayName")
    console.print("[green]Garmin session saved.[/green]")
    console.print(f"Account: {account}")
    console.print(f"Display name: {display_name if isinstance(display_name, str) else 'unknown'}")
    console.print(f"Session: {garmin_session_path()}")


@garmin_app.command("status")
def garmin_status(
    account: Annotated[str, typer.Option(help="Garmin account email.")],
) -> None:
    """Check whether the saved Garmin session can read profile data."""
    client = _garmin_client(account)
    try:
        client.ensure_session(allow_login=False)
        profile = client.profile()
    except GarminError as exc:
        raise typer.Exit(_print_error(exc)) from exc
    console.print("[green]Garmin session is valid.[/green]")
    display_name = profile.get("displayName")
    console.print(f"Display name: {display_name if isinstance(display_name, str) else 'unknown'}")
    console.print(f"Profile keys: {', '.join(sorted(profile.keys()))}")


@garmin_app.command("doctor")
def garmin_doctor(
    account: Annotated[str, typer.Option(help="Garmin account email.")],
) -> None:
    """Check local Garmin credential and session setup without printing secrets."""
    table = Table(title="Garmin Local Setup")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail")

    config = GarminConfig(account=account)
    try:
        _ = read_keychain_internet_password(config.keychain_server, config.account)
        table.add_row("Keychain", "ok", f"{config.account} at {config.keychain_server}")
    except SecretError as exc:
        table.add_row("Keychain", "missing", str(exc))

    session_path = garmin_session_path()
    if session_path.exists():
        mode = session_path.stat().st_mode & 0o777
        mode_status = "ok" if mode == 0o600 else "check"
        table.add_row("Session file", mode_status, f"{session_path} mode {mode:o}")
    else:
        table.add_row("Session file", "missing", str(session_path))

    console.print(table)


@garmin_app.command("sync")
def garmin_sync(
    account: Annotated[str, typer.Option(help="Garmin account email.")],
    start: Annotated[str | None, typer.Option("--from", help="Start date, inclusive, YYYY-MM-DD.")] = None,
    end: Annotated[str | None, typer.Option("--to", help="End date, inclusive, YYYY-MM-DD.")] = None,
    latest_days: Annotated[int, typer.Option(help="Used when --from is omitted.")] = 7,
    activities: Annotated[int, typer.Option(help="Number of latest activity summaries to sync.")] = 25,
    details: Annotated[bool, typer.Option(help="Fetch activity detail JSON for each synced activity.")] = True,
    login: Annotated[bool, typer.Option(help="Allow login from Keychain if saved session is missing/expired.")] = False,
) -> None:
    """Sync Garmin data using read-only GET endpoints only."""
    today = date.today()
    start_day = _parse_date_option(start) if start is not None else (today - timedelta(days=latest_days - 1))
    end_day = _parse_date_option(end) if end is not None else today

    client = _garmin_client(account)
    store = _store()
    service = GarminSyncService(client, store)
    try:
        client.ensure_session(allow_login=login)
        recoveries = service.sync_daily_range(start_day, end_day)
        activity_sessions = service.sync_activities(limit=activities, include_details=details)
    except GarminError as exc:
        raise typer.Exit(_print_error(exc)) from exc

    console.print("[green]Garmin sync complete.[/green]")
    console.print(f"Daily recovery rows synced: {len(recoveries)}")
    console.print(f"Activities synced: {len(activity_sessions)}")
    console.print(f"SQLite: {sqlite_path()}")


@import_app.command("strong")
def import_strong(
    path: Annotated[Path, typer.Argument(help="Strong CSV export path.")],
) -> None:
    """Import historical Strong workout sets."""
    store = _store()
    try:
        lifting_sets = import_strong_csv(path)
    except StrongImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    store.upsert_lifting_sets(lifting_sets)
    console.print("[green]Strong import complete.[/green]")
    console.print(f"Sets imported: {len(lifting_sets)}")
    console.print(f"SQLite: {sqlite_path()}")


@import_app.command("dexa")
def import_dexa(
    path: Annotated[Path, typer.Argument(help="BodySpec PDF file or folder.")],
) -> None:
    """Import BodySpec DEXA PDF summary and regional data."""
    store = _store()
    try:
        scans = import_bodyspec_path(path)
    except DexaImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    store.upsert_dexa_scans(scans)
    console.print("[green]DEXA import complete.[/green]")
    console.print(f"Scans imported: {len(scans)}")
    console.print(f"SQLite: {sqlite_path()}")


@app.command("state")
def state(
    limit: Annotated[int, typer.Option(help="Rows to show.")] = 7,
) -> None:
    """Print latest normalized Garmin recovery and activity state."""
    store = _store()
    counts = store.counts()
    console.print(f"SQLite: {sqlite_path()}")
    console.print(f"Counts: {counts}")

    recovery_table = Table(title="Latest Daily Recovery")
    for col in ["date", "sleep", "hrv", "rhr", "stress", "bb", "confidence"]:
        recovery_table.add_column(col)
    for row in store.latest_daily_recovery(limit):
        recovery_table.add_row(
            row.date.isoformat(),
            str(row.sleep_minutes),
            str(row.hrv_ms),
            str(row.resting_heart_rate_bpm),
            str(row.stress_score),
            f"{row.body_battery_min}/{row.body_battery_max}",
            f"{row.source_confidence:.2f}",
        )
    console.print(recovery_table)

    activity_table = Table(title="Latest Activities")
    for col in ["started", "sport", "name", "mins", "avg_hr", "load"]:
        activity_table.add_column(col)
    for row in store.latest_activities(limit):
        activity_table.add_row(
            row.started_at.isoformat(sep=" ", timespec="minutes"),
            row.sport.value,
            row.name or "",
            f"{row.duration_minutes:.1f}",
            str(row.average_heart_rate_bpm),
            str(row.training_load),
        )
    console.print(activity_table)


@inspect_app.command("strength")
def inspect_strength(
    limit: Annotated[int, typer.Option(help="Rows to show.")] = 20,
) -> None:
    """Print the highest imported Strong e1RM observations."""
    table = Table(title="Top Strength Estimates")
    for col in ["exercise", "e1RM", "weight", "reps", "observed"]:
        table.add_column(col)
    for row in _store().top_strength_estimates(limit):
        table.add_row(
            row.exercise_name,
            f"{row.estimated_1rm_lb:.1f}",
            f"{row.weight_lb:.1f}",
            f"{row.reps:.1f}",
            row.observed_at.date().isoformat(),
        )
    console.print(table)


@inspect_app.command("dexa")
def inspect_dexa(
    limit: Annotated[int, typer.Option(help="Rows to show.")] = 5,
) -> None:
    """Print latest imported DEXA scan summaries."""
    table = Table(title="Latest DEXA Scans")
    for col in ["date", "mass", "fat %", "lean", "arms", "legs", "trunk"]:
        table.add_column(col)
    for scan in _store().latest_dexa_scans(limit):
        arms = scan.regions.get("arms")
        legs = scan.regions.get("legs")
        trunk = scan.regions.get("trunk")
        table.add_row(
            scan.measured_date.isoformat(),
            f"{scan.total_mass_lb:.1f}",
            f"{scan.total_body_fat_percent:.1f}",
            f"{scan.lean_tissue_lb:.1f}",
            f"{arms.lean_tissue_lb:.1f}" if arms is not None else "",
            f"{legs.lean_tissue_lb:.1f}" if legs is not None else "",
            f"{trunk.lean_tissue_lb:.1f}" if trunk is not None else "",
        )
    console.print(table)


@inspect_app.command("development")
def inspect_development() -> None:
    """Print DEXA and key-lift signals used for development priorities."""
    store = _store()
    scans = store.latest_dexa_scans(20)
    lifting_sets = store.all_lifting_sets()

    if scans:
        _print_dexa_development(scans)
    else:
        console.print("[yellow]No DEXA scans imported.[/yellow]")

    if lifting_sets:
        _print_key_lift_development(scans[0] if scans else None, lifting_sets)
    else:
        console.print("[yellow]No lifting sets imported.[/yellow]")


def _print_error(exc: GarminError) -> int:
    console.print(f"[red]{exc}[/red]")
    return 1


def _parse_date_option(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        console.print(f"[red]Invalid date {value!r}; expected YYYY-MM-DD.[/red]")
        raise typer.Exit(1) from exc


def _mfa_callback(mfa_code: str | None) -> Callable[[], str] | None:
    if mfa_code is None:
        return None

    def callback() -> str:
        return mfa_code

    return callback


def _print_dexa_development(scans: list[DexaScan]) -> None:
    latest = scans[0]
    previous = scans[1] if len(scans) > 1 else None
    table = Table(title="DEXA Lean-Mass Signals")
    for col in ["region", "latest", "vs previous", "vs best"]:
        table.add_column(col)
    for region in ["arms", "legs", "trunk"]:
        latest_region = latest.regions.get(region)
        if latest_region is None:
            continue
        previous_region = previous.regions.get(region) if previous is not None else None
        best_value = max(
            scan.regions[region].lean_tissue_lb
            for scan in scans
            if region in scan.regions
        )
        previous_delta = (
            latest_region.lean_tissue_lb - previous_region.lean_tissue_lb
            if previous_region is not None
            else None
        )
        best_delta = latest_region.lean_tissue_lb - best_value
        table.add_row(
            region,
            f"{latest_region.lean_tissue_lb:.1f} lb",
            _format_delta(previous_delta),
            _format_delta(best_delta),
        )
    console.print(table)


def _print_key_lift_development(latest_scan: DexaScan | None, lifting_sets: list[LiftingSet]) -> None:
    body_mass = latest_scan.total_mass_lb if latest_scan is not None else None
    recent_cutoff = datetime.now() - timedelta(days=180)
    table = Table(title="Key Lift Anchors")
    for col in ["lift", "recent e1RM", "best e1RM", "recent/BW", "anchor date"]:
        table.add_column(col)
    for label, aliases in _KEY_LIFT_ALIASES.items():
        recent = _best_lifting_set(lifting_sets, aliases, since=recent_cutoff)
        best = _best_lifting_set(lifting_sets, aliases, since=None)
        anchor = recent if recent is not None else best
        table.add_row(
            label,
            _format_lift_estimate(recent),
            _format_lift_estimate(best),
            _format_bodyweight_ratio(recent, body_mass),
            anchor.started_at.date().isoformat() if anchor is not None else "",
        )
    console.print(table)


def _best_lifting_set(lifting_sets: list[LiftingSet], exercise_names: list[str], *, since: datetime | None) -> LiftingSet | None:
    candidates = [
        lifting_set
        for lifting_set in lifting_sets
        if lifting_set.exercise_name in exercise_names
        and lifting_set.estimated_1rm_lb is not None
        and (since is None or lifting_set.started_at >= since)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda lifting_set: lifting_set.estimated_1rm_lb or 0.0)


def _format_lift_estimate(lifting_set: LiftingSet | None) -> str:
    if lifting_set is None or lifting_set.estimated_1rm_lb is None:
        return ""
    return f"{lifting_set.estimated_1rm_lb:.1f}"


def _format_bodyweight_ratio(lifting_set: LiftingSet | None, body_mass: float | None) -> str:
    if lifting_set is None or lifting_set.estimated_1rm_lb is None or body_mass is None:
        return ""
    return f"{lifting_set.estimated_1rm_lb / body_mass:.2f}x"


def _format_delta(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:+.1f} lb"


if __name__ == "__main__":
    app()
