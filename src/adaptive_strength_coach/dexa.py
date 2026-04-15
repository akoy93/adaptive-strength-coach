from __future__ import annotations

import re
import subprocess
from datetime import date, datetime
from pathlib import Path

from adaptive_strength_coach.models import DexaRegion, DexaScan


class DexaImportError(RuntimeError):
    """Raised when a BodySpec DEXA PDF cannot be imported."""


_NUMBER = r"\d+(?:\.\d+)?"
_SUMMARY_RE = re.compile(
    rf"^\s*(?P<date>\d{{1,2}}/\d{{1,2}}/\d{{4}})\s+"
    rf"(?P<body_fat>{_NUMBER})%\s+"
    rf"(?P<total_mass>{_NUMBER})\s+"
    rf"(?P<fat_tissue>{_NUMBER})\s+"
    rf"(?P<lean_tissue>{_NUMBER})\s+"
    rf"(?P<bmc>{_NUMBER})\s*$",
    re.MULTILINE,
)
_REGION_RE = re.compile(
    rf"^\s*(?P<region>Arms|Legs|Trunk|Android|Gynoid|Total)\s+"
    rf"(?P<body_fat>{_NUMBER})%?\s+"
    rf"(?P<total_mass>{_NUMBER})\s+"
    rf"(?P<fat_tissue>{_NUMBER})\s+"
    rf"(?P<lean_tissue>{_NUMBER})\s+"
    rf"(?P<bmc>{_NUMBER})\s*$",
    re.MULTILINE,
)


def import_bodyspec_path(path: Path) -> list[DexaScan]:
    pdf_paths = _pdf_paths(path)
    scans_by_date: dict[date, DexaScan] = {}
    for pdf_path in pdf_paths:
        for scan in import_bodyspec_pdf(pdf_path):
            existing = scans_by_date.get(scan.measured_date)
            scans_by_date[scan.measured_date] = _merge_scan(existing, scan) if existing is not None else scan
    return sorted(scans_by_date.values(), key=lambda scan: scan.measured_date)


def import_bodyspec_pdf(path: Path) -> list[DexaScan]:
    text = _pdftotext(path)
    return parse_bodyspec_text(text, source_name=str(path))


def parse_bodyspec_text(text: str, *, source_name: str = "BodySpec text") -> list[DexaScan]:
    summary_scans = _parse_summary_scans(text)
    if not summary_scans:
        raise DexaImportError(f"No BodySpec summary rows found in {source_name}")

    current_scan_date = summary_scans[-1].measured_date
    first_summary_match = _SUMMARY_RE.search(text)
    if first_summary_match is not None:
        current_scan_date = _parse_date(first_summary_match.group("date"))
    regions = _parse_regions(text)

    if regions:
        summary_scans = [
            scan.model_copy(update={"regions": regions}) if scan.measured_date == current_scan_date else scan
            for scan in summary_scans
        ]
    return summary_scans


def _pdf_paths(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise DexaImportError(f"Expected a PDF file: {path}")
        return [path]
    if path.is_dir():
        return sorted(child for child in path.iterdir() if child.suffix.lower() == ".pdf")
    raise DexaImportError(f"DEXA path not found: {path}")


def _pdftotext(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DexaImportError("pdftotext is required to import BodySpec PDFs.") from exc
    if result.returncode != 0:
        raise DexaImportError(f"pdftotext failed for {path}: {result.stderr.strip()}")
    return result.stdout


def _parse_summary_scans(text: str) -> list[DexaScan]:
    scans: list[DexaScan] = []
    for match in _SUMMARY_RE.finditer(text):
        scans.append(
            DexaScan(
                measured_date=_parse_date(match.group("date")),
                total_body_fat_percent=float(match.group("body_fat")),
                total_mass_lb=float(match.group("total_mass")),
                fat_tissue_lb=float(match.group("fat_tissue")),
                lean_tissue_lb=float(match.group("lean_tissue")),
                bone_mineral_content_lb=float(match.group("bmc")),
            )
        )
    return scans


def _parse_regions(text: str) -> dict[str, DexaRegion]:
    regions: dict[str, DexaRegion] = {}
    for match in _REGION_RE.finditer(text):
        region = match.group("region").lower()
        regions[region] = DexaRegion(
            region=region,
            total_region_fat_percent=float(match.group("body_fat")),
            total_mass_lb=float(match.group("total_mass")),
            fat_tissue_lb=float(match.group("fat_tissue")),
            lean_tissue_lb=float(match.group("lean_tissue")),
            bone_mineral_content_lb=float(match.group("bmc")),
        )
    return regions


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%m/%d/%Y").date()


def _merge_scan(existing: DexaScan | None, incoming: DexaScan) -> DexaScan:
    if existing is None:
        return incoming
    if incoming.regions:
        return incoming
    return existing
