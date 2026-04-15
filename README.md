# Adaptive Strength Coach CLI

## Summary

Build a CLI-first tool focused on data ingestion, training-state modeling, and recommendation outputs. The first version intentionally has no frontend. The goal is to validate the data pipeline and recommendation algorithm before investing in a UI.

The CLI should import historical Strong and DEXA data, sync or load Garmin, Oura, and EightSleep data through provider adapters, compute current training state, and print a daily workout recommendation with target exercises, sets, reps, loads, and reasons.

The MVP should verify:

- Imported data is correct and auditable.
- Garmin, Oura, and EightSleep inputs normalize into useful recovery and activity signals.
- DEXA scans and lifting numbers produce reasonable development priorities.
- Daily recommendations look like sound strength programming adjusted for recovery, sport load, and user feedback.

## Current Implementation

The repo currently includes the first read-only Garmin integration slice:

```bash
uv sync --dev
uv run coach garmin doctor --account "$GARMIN_EMAIL"
uv run coach garmin auth --account "$GARMIN_EMAIL"
uv run coach garmin status --account "$GARMIN_EMAIL"
uv run coach garmin sync --account "$GARMIN_EMAIL" --latest-days 1 --activities 3 --details
uv run coach import strong /Users/akoy/Desktop/health/strong_workouts.csv
uv run coach import dexa /Users/akoy/Desktop/health
uv run coach state
uv run coach inspect dexa
uv run coach inspect strength
uv run coach inspect development
```

Credential handling:

- Garmin credentials are read from the macOS Keychain internet-password entry for `sso.garmin.com`.
- The CLI never prints the password.
- Garmin session tokens are stored locally at `~/.adaptive-strength-coach/garmin/session.json` with `0600` permissions.
- Raw Garmin JSON audit payloads are stored under `~/.adaptive-strength-coach/raw/garmin` with `0600` permissions.
- The SQLite database is stored at `~/.adaptive-strength-coach/coach.sqlite3`.

Garmin safety constraints:

- The adapter only uses read-only Garmin Connect requests.
- It does not call upload, update, delete, or workout-publishing endpoints.
- `404` activity-detail misses fall back to activity summaries.
- `429` rate limits abort the sync and should not be retried aggressively.
- If Garmin asks for MFA, rerun `coach garmin auth` with `--mfa-code`.

Bootstrap imports:

- Strong CSV imports skip rest-timer rows and store normalized working sets.
- Strong e1RM estimates use the Epley formula for sets of 1-12 reps.
- BodySpec PDFs are parsed through local `pdftotext`; summary and regional lean-mass fields are persisted.
- `coach inspect development` combines DEXA lean-mass trends with key lift anchors.

## Product Scope

The app is a coaching and planning tool, not a medical tool. It should generate a custom workout each session rather than selecting from fixed templates. Biometrics and machine learning may adjust training dose, but strength programming principles determine the training direction.

Primary goals:

- Maintain and gradually improve strength.
- Support muscle mass and aesthetics.
- Respect tennis, cycling, golf, walking, sleep, and stress as meaningful recovery constraints.
- Learn over time how the user's biomarkers correlate with manual fatigue reports and workout outcomes.

Non-goals for v1:

- No frontend.
- No workout publishing to Garmin or another app.
- No black-box exercise generation without inspectable reasons.
- No dependence on Strong after historical bootstrap.

## CLI Commands

```bash
coach init
coach import strong /Users/akoy/Desktop/health/strong_workouts.csv
coach import dexa /Users/akoy/Desktop/health
coach sync garmin
coach sync oura
coach sync eight-sleep
coach state today
coach recommend today
coach recommend --date 2026-04-15
coach log-workout
coach log-fatigue
coach inspect strength
coach inspect recovery
coach inspect development
```

## Implementation Shape

Use a small Python CLI with strict typed domain models.

Recommended stack:

- `typer` for CLI commands.
- `pydantic` for explicit schemas.
- SQLite for local persistence.
- Typed repository wrappers or `sqlmodel` for database access.
- `pandas` only for import and analysis helpers, not core domain logic.
- `scikit-learn` behind a feature flag once enough feedback data exists.
- `pytest` for parser, scoring, load, and recommendation tests.

Persist:

- Raw provider payloads for auditability.
- Normalized daily recovery inputs.
- Normalized activity sessions.
- Strong-imported historical lifting sessions.
- Native app lifting sessions.
- DEXA scans.
- Derived recovery state.
- Derived strength estimates.
- Derived development priorities.
- Generated recommendations.
- User fatigue and post-workout feedback.

## Data And Integrations

### Garmin

Use Garmin as the primary source for sport and external activity load.

Pull:

- Tennis, cycling, golf, walking, and other activity sessions.
- Duration, distance, average HR, max HR, and training load when available.
- Steps, stress, Body Battery, resting HR, HRV status, and sleep when available.

Use Garmin to estimate systemic, lower-body, grip, shoulder/elbow, and sport-specific fatigue.

### Oura

Use Oura as a primary source for sleep and recovery.

Pull:

- Sleep duration and timing.
- Readiness and sleep scores.
- HRV.
- Resting heart rate.
- Respiratory rate.
- Temperature deviation.
- Tags when available.

### EightSleep

Use EightSleep as an additional sleep and recovery signal.

Pull:

- Sleep duration.
- Wake events.
- HR and HRV.
- Respiratory rate.
- Sleep quality fields.
- Thermal events.

Normalize EightSleep and Oura into one sleep recovery model. Do not double-count the same night's sleep stress if both devices report it.

### Strong Bootstrap

Import the Strong CSV once.

Use it to initialize:

- Exercise list and aliases.
- Historical session frequency.
- Recent and historical estimated 1RM values.
- Per-exercise working loads.
- Movement-pattern exposure.
- Rough volume tolerance.

Strong is read-only historical context. New workouts are logged in the CLI.

### DEXA Import

Import BodySpec DEXA scans.

Extract:

- Scan date.
- Total mass.
- Body fat percentage.
- Fat mass.
- Lean tissue.
- Bone mineral content.
- Regional arms, legs, trunk, android, and gynoid values.

Use DEXA for broad development trends:

- If lean mass is down while strength is stable, bias toward hypertrophy.
- If strength is down while lean mass is stable, bias toward strength exposure.
- If legs, trunk, or arms trend down, modestly raise related mesocycle-level priorities.
- Do not treat DEXA as precise muscle-by-muscle diagnosis.

Initial anchors from the provided data:

- Latest DEXA scan on 2026-03-24: 167.5 lb body mass, 13.4% body fat, 137.0 lb lean tissue.
- Latest regional lean mass: arms 18.6 lb, legs 51.3 lb, trunk 59.9 lb.
- Recent e1RM approximations: bench about 220 lb, squat about 320 lb, overhead press about 145 lb, barbell row about 195 lb, RDL about 245 lb, weighted pull-up about 45-50 lb added.

## Normalized Models

```python
from datetime import date, datetime
from enum import StrEnum
from pydantic import BaseModel, Field


class Provider(StrEnum):
    GARMIN = "garmin"
    OURA = "oura"
    EIGHT_SLEEP = "eight_sleep"
    STRONG_IMPORT = "strong_import"
    MANUAL = "manual"


class Sport(StrEnum):
    TENNIS = "tennis"
    CYCLING = "cycling"
    GOLF = "golf"
    WALKING = "walking"
    LIFTING = "lifting"
    OTHER = "other"


class DailyRecoveryInput(BaseModel):
    date: date
    provider: Provider
    sleep_minutes: int | None = None
    sleep_score: float | None = None
    readiness_score: float | None = None
    hrv_ms: float | None = None
    resting_heart_rate_bpm: float | None = None
    respiratory_rate: float | None = None
    temperature_deviation: float | None = None
    stress_score: float | None = None
    body_battery: float | None = None
    wake_events: int | None = None
    source_confidence: float = Field(ge=0.0, le=1.0)


class ActivitySession(BaseModel):
    id: str
    provider: Provider
    started_at: datetime
    sport: Sport
    duration_minutes: int
    average_heart_rate_bpm: float | None = None
    max_heart_rate_bpm: float | None = None
    training_load: float | None = None
    perceived_intensity: int | None = Field(default=None, ge=1, le=5)


class LiftingSet(BaseModel):
    exercise_id: str
    target_weight_lb: float
    target_reps: int
    target_rpe: float | None = None
    completed_weight_lb: float | None = None
    completed_reps: int | None = None
    completed_rpe: float | None = None
    rir: float | None = None
```

## Programming Model

Model training around movement patterns, recoverable volume, development priorities, strength exposure, and fatigue costs.

Movement patterns:

- Squat or knee dominant.
- Hinge or posterior chain.
- Trap bar deadlift.
- Horizontal push.
- Vertical push.
- Horizontal pull.
- Vertical pull.
- Lateral delts.
- Biceps.
- Triceps.
- Core.

Exercise pool:

- Squat: back squat, front squat, leg press.
- Hinge: Romanian deadlift, hip thrust, back extension.
- Trap bar: trap bar deadlift.
- Horizontal push: bench press, incline bench press, dumbbell bench press.
- Vertical push: barbell overhead press, dumbbell overhead press.
- Horizontal pull: barbell row, T-bar row, chest-supported row, cable row.
- Vertical pull: weighted pull-up, bodyweight pull-up, pulldown.
- Accessories: Egyptian cable lateral raise, preacher curl, triceps pushdown or extension, light core.

Balanced weekly exposure targets:

- Squat or knee dominant: 1 exposure if recovery allows.
- Hinge or trap bar: 1 exposure if recovery allows.
- Horizontal push: 1-2 exposures.
- Vertical push: 1 exposure.
- Horizontal pull: 1-2 exposures.
- Vertical pull: 1 exposure.
- Delts, biceps, triceps: 1-2 small exposures.
- Core: 1-2 small exposures.

These are targets, not rigid quotas. Tennis, cycling, golf, poor sleep, soreness, and pain can shift the week.

Underrepresented exercises in the Strong export do not receive artificial priority. Sparse history means lower confidence and more conservative starting loads.

## Daily Algorithm

Generate each recommendation in six passes.

### 1. Build Baselines

For each biometric:

```text
today value
7-day acute average
28-day baseline
acute-to-baseline ratio
deviation from personal norm
source confidence
```

Key signals:

- HRV below baseline.
- Resting HR above baseline.
- Short or fragmented sleep.
- High stress.
- Low Body Battery.
- High recent sport load.
- Elevated user-reported fatigue.

### 2. Compute Recovery Vectors

Compute regional recovery rather than one generic readiness score:

```text
systemic_recovery
lower_body_recovery
posterior_chain_recovery
upper_push_recovery
upper_pull_recovery
shoulder_elbow_recovery
grip_forearm_recovery
```

Use decaying fatigue:

```text
remaining_fatigue = initial_fatigue * exp(-hours_since_activity / half_life)
```

Default half-lives:

- Heavy squat: 48-72 hours.
- RDL or trap bar deadlift: 48-72 hours.
- Hard tennis: 36-60 hours for lower body, grip, shoulder/elbow.
- Long cycling: 24-48 hours.
- Golf walking: 12-24 hours.
- Heavy pressing or pulling: 24-48 hours.
- Accessories: 12-24 hours.

### 3. Compute Development Priorities

Use lifting numbers and DEXA to identify what deserves more attention over the mesocycle.

```text
development_priority =
  strength_ratio_gap
+ historical_regression_gap
+ recent_progress_staleness
+ DEXA_regional_lag_or_decline
+ goal_relevance
```

Compare:

- Squat, bench, overhead press, row, RDL, trap bar deadlift, and weighted pull-up against bodyweight.
- Current e1RM against historical bests.
- Overhead press to bench ratio.
- Row to bench ratio.
- Hinge or trap bar strength relative to squat.
- Pulling exposure relative to pressing exposure.
- Regional lean-mass trends from DEXA.

Interpretation rules:

- If a DEXA region is flat or declining and recovery supports more volume, increase the mesocycle volume target for related movement patterns.
- If a lift ratio is lagging but DEXA looks fine, bias toward strength exposure.
- If a DEXA region is lagging but strength is fine, bias toward hypertrophy volume.
- If sport load repeatedly blocks a lagging pattern, prescribe lower-fatigue variants to maintain exposure.

### 4. Compute Training Need

For each movement pattern:

```text
training_need =
  goal_priority
+ days_since_meaningful_exposure
+ weekly_volume_deficit
+ strength_staleness
+ development_priority
- local_fatigue
- sport_interference
- joint_risk
```

Meaningful exposure:

- Main movement: 2 or more working sets at RPE 7 or higher.
- Hypertrophy movement: 3 or more working sets at RPE 7 or higher.
- Accessory: 2 or more direct sets.

### 5. Assign Training Budget

Create separate budgets:

```text
systemic_budget
lower_body_budget
posterior_chain_budget
upper_body_budget
shoulder_elbow_budget
grip_budget
time_budget
```

Decision bands:

```text
green:
  normal session
  RPE cap 8-8.5

yellow:
  modified productive session
  RPE cap 7.5-8
  reduce load 3-8% or volume 10-25%

orange:
  low-fatigue session
  RPE cap 6.5-7.5
  reduce load 10-20% and volume 30-50%

red:
  active recovery or rest
```

Sport-specific constraints:

- Hard tennis in the last 24 hours: avoid heavy squat, RDL, and trap bar deadlift unless lower-body recovery is green.
- Tennis later today: avoid heavy lower-body and avoid high grip, shoulder, or elbow fatigue.
- Long or intense cycling in the last 24 hours: reduce lower-body volume before upper-body volume.
- Golf walking alone: usually minor adjustment unless paired with poor sleep or high fatigue.

### 6. Generate Custom Workout

Select exercises under constraints rather than choosing a fixed template.

```text
candidate_score =
  movement_training_need
+ development_priority
+ goal_relevance
+ freshness
+ user_preference
- systemic_fatigue_cost
- regional_fatigue_cost
- sport_interference_cost
- joint_risk
```

Session rules:

- Usually 3-6 exercises.
- Usually 1 main strength movement if readiness allows.
- Usually 1-2 secondary compounds.
- Usually 1-3 accessories.
- Avoid more than one high-fatigue lower-body lift unless recovery is excellent.
- Prefer T-bar, chest-supported, or cable rows when posterior chain is fatigued.
- Prefer upper hypertrophy and accessories when tennis or cycling limits lower body.

Every recommendation should explain itself:

- Why this workout decision was selected.
- Why each exercise was selected.
- Why the target loads were adjusted.
- Which data sources were missing or low confidence.
- What would change the recommendation.

## Load Prescription

Use Strong import to initialize, then app data takes over.

For each exercise:

```text
e1RM = weight * (1 + reps / 30)
current_strength = recency_weighted_top_cluster_e1RM
training_max = current_strength * 0.90 to 0.95
```

Track separate anchors for:

- Squat.
- Bench.
- Overhead press.
- Romanian deadlift.
- Trap bar deadlift.
- Weighted pull-up.
- Barbell row.
- T-bar row after enough direct data exists.

Sparse exercises:

- Initialize conservatively from related lifts.
- Use calibration sessions at RPE 6-7.
- Increase confidence only after repeated logged exposures.

### Main Lifts

```text
top set:
  1 x 3-6 @ RPE 7-8

backoff:
  2-4 x 5-8
  88-92% of top set load
```

Approximate target intensities:

```text
3 reps @ RPE 8: 87-90% e1RM
5 reps @ RPE 8: 82-85% e1RM
6 reps @ RPE 8: 80-82% e1RM
8 reps @ RPE 8: 75-78% e1RM
```

Final load:

```text
base_load = e1RM_percent_for_target_reps
readiness_load = base_load * readiness_modifier
sport_load = readiness_load * sport_modifier
final_load = round_to_available_increment(sport_load)
```

Modifiers:

```text
green: 1.00
yellow: 0.92-0.97
orange: 0.80-0.90
red: no strength work
```

Prefer volume reduction when the athlete is generally tired. Prefer load reduction when joints, soreness, or technique risk are the concern.

### Hypertrophy Compounds

For rows, incline press, dumbbell press, hip thrust, leg press, and pull-ups:

```text
2-4 sets
6-12 reps
RPE 7-9
double progression
```

Progress reps first, then load.

### Accessories

For laterals, preacher curls, triceps, and core:

```text
2-4 sets
10-20 reps
RPE 8-9
```

Accessories should mostly be limited by joint irritation, time, and local fatigue, not by mild systemic fatigue.

## CLI Output

`coach state today` should print:

- Latest recovery state.
- Recent Garmin, Oura, and EightSleep inputs.
- Recent sport load.
- Movement freshness.
- Current strength anchors.
- Current development priorities.
- Missing or low-confidence data warnings.

`coach recommend today` should print:

```text
Decision: Lift / Modified Lift / Active Recovery / Rest
Confidence: 0-100

Why:
- Lower-body recovery is yellow because tennis load was high yesterday.
- Hinge strength is lagging relative to squat and historical RDL.
- Sleep recovery is normal; systemic budget is green.
- Shoulder/elbow budget is green, so upper pulling is allowed.

Workout:
1. Bench Press
   Top set: 1 x 5 @ 180 lb, RPE 8
   Backoff: 3 x 6 @ 165 lb
   Reason: horizontal push is due; readiness supports strength work.

2. T-Bar Row
   3 x 8-10 @ conservative calibration load
   Reason: pulling balance and back hypertrophy; posterior chain cost lower than barbell row.

3. Romanian Deadlift
   2 x 8 @ reduced load
   Reason: hinge priority exists, but posterior-chain budget is yellow.

4. Egyptian Cable Lateral Raise
   3 x 12-15
   Reason: low systemic cost hypertrophy work.

5. Triceps Pushdown
   2 x 12-15
   Reason: direct triceps volume with low recovery cost.
```

The output should also include alternative substitutions and data confidence warnings.

## Machine Learning

Use machine learning after enough data exists, while keeping deterministic programming guardrails.

Collect with the CLI:

```bash
coach log-fatigue
coach complete-workout
```

Fields:

```text
morning_fatigue: 1-5
motivation: 1-5
leg_soreness: 0-3
posterior_chain_soreness: 0-3
upper_soreness: 0-3
shoulder_elbow_status: 0-3
pain_flag: true/false
post_workout_difficulty: too_easy/right/too_hard
```

Train models:

```text
biometrics + sport load + recent lifting load
  -> predicted fatigue

biometrics + sport load + planned workout stress
  -> probability workout is too easy / right / too hard

biometrics + recent performance
  -> load and volume modifier
```

Initial ML:

- Ridge or logistic regression for interpretability.
- Bayesian source weighting for Garmin vs Oura vs EightSleep.
- Gradient boosted trees later if data volume justifies it.

Guardrails:

- ML can adjust load and volume within caps.
- ML can lower the recommendation to recovery.
- ML cannot override pain flags.
- ML cannot violate basic programming rules.
- Low confidence falls back to rules.

## Progression Rules

Main lifts:

```text
if top set hits target reps at or below target RPE:
  next exposure add 2.5-5 lb or add 1 rep

if top set overshoots target RPE by 1 or more:
  repeat load next exposure

if two poor exposures occur:
  reduce training max by 2.5-5%
```

Hypertrophy:

```text
if all sets hit top of rep range at target RPE:
  add smallest useful load next exposure
else:
  repeat load and add reps
```

Fatigue calibration:

```text
if user repeatedly says "too hard":
  reduce similar future load/volume modifiers

if user repeatedly says "too easy":
  increase similar future load/volume modifiers

if performance beats prediction:
  update strength estimate upward

if performance misses despite good readiness:
  check whether training max is too high or local fatigue was underestimated
```

## Tests

Parser tests:

- Strong CSV imports all rows and sessions.
- DEXA importer extracts all scan dates and latest values.
- Provider adapters normalize Garmin, Oura, and EightSleep records.
- Duplicate Oura and EightSleep sleep nights merge correctly.

Algorithm tests:

- Green readiness, squat due, no sport fatigue: generates squat-centered session.
- Hinge lagging and posterior chain fresh: chooses RDL or trap bar.
- Hard tennis yesterday: avoids heavy squat, RDL, and trap bar.
- Tennis later today: avoids heavy lower-body and high grip or shoulder stress.
- Cycling yesterday: reduces lower-body volume before upper-body volume.
- DEXA leg lean mass down plus lower recovery green: biases lower-body work.
- DEXA arms or delts flat plus upper recovery green: includes direct upper hypertrophy.
- Sparse trap-bar or T-bar history: prescribes conservative calibration load.
- Poor sleep, low HRV, high resting HR: downshifts load/volume or recommends recovery.
- User repeatedly says "too hard": similar future days are downshifted.
- User repeatedly says "too easy": similar future days become less conservative.

Load tests:

- e1RM ignores warmups and stale anomalies.
- Loads round to available increments.
- Backoffs derive from top-set load.
- Failed exposures reduce training max.
- Successful low-RPE exposures increase future target.

## Assumptions

- No frontend in v1.
- CLI output is the product surface for now.
- Garmin, Oura, and EightSleep APIs are available through adapters.
- Strong is bootstrap-only.
- Future workouts are logged natively in the CLI.
- DEXA influences development priorities at the mesocycle level.
- Underrepresented exercises in Strong are treated as low-confidence load estimates, not priority bonuses.
- Machine learning is allowed for personalization but constrained by explicit strength-programming rules.
- Biometrics and machine learning adjust training dose; programming logic determines training direction.
