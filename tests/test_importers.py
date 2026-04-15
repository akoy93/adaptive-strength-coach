from __future__ import annotations

from pathlib import Path

from adaptive_strength_coach.dexa import parse_bodyspec_text
from adaptive_strength_coach.strong import import_strong_csv


def test_import_strong_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "strong.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Date,Workout Name,Duration,Exercise Name,Set Order,Weight,Reps,Distance,Seconds,Notes,Workout Notes,RPE",
                '2026-04-15 07:30:00,"Upper",1h 5m,"Bench Press (Barbell)",1,185.0,5.0,0,0.0,"","",8',
                '2026-04-15 07:30:00,"Upper",1h 5m,"Bench Press (Barbell)",Rest Timer,0,0,0,180.0,"","",',
                '2026-04-16 07:30:00,"",45m,"Front Squat",1,250.0,3.0,0,0.0,"","",',
            ]
        ),
        encoding="utf-8",
    )

    rows = import_strong_csv(csv_path)

    assert len(rows) == 2
    assert rows[0].workout_name == "Upper"
    assert rows[0].duration_minutes == 65
    assert rows[0].exercise_name == "Bench Press (Barbell)"
    assert rows[0].weight_lb == 185
    assert rows[0].reps == 5
    assert rows[0].rpe == 8
    assert rows[0].estimated_1rm_lb == 215.8
    assert rows[1].workout_name == "Unnamed Workout"
    assert rows[1].estimated_1rm_lb == 275


def test_parse_bodyspec_text_with_regions() -> None:
    text = """
    SUMMARY RESULTS
      3/24/2026                         13.4%                167.5                22.5                 137.0                    8.0
     12/22/2025                         13.9%                169.5                23.6                 137.9                    8.0

    REGIONAL ASSESSMENT
      Arms          11.3%            22.3             2.5             18.6             1.3
      Legs          13.9%            63.2             8.8             51.3             3.1
      Trunk         12.9%            71.5             9.2             59.9             2.4
      Android        10.3%            10.2             1.0              9.0             0.2
      Gynoid         13.5%            28.1             3.7             23.5             0.9
      Total         13.4%           167.5            22.5            137.0             8.0
    """

    scans = parse_bodyspec_text(text)

    assert len(scans) == 2
    latest = scans[0]
    assert latest.measured_date.isoformat() == "2026-03-24"
    assert latest.total_body_fat_percent == 13.4
    assert latest.lean_tissue_lb == 137.0
    assert latest.regions["arms"].lean_tissue_lb == 18.6
    assert latest.regions["legs"].lean_tissue_lb == 51.3
    assert scans[1].regions == {}
