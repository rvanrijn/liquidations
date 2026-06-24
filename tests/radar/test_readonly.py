from pathlib import Path

RADAR_DIR = Path(__file__).resolve().parents[2] / "src" / "radar"

FORBIDDEN = [
    "battle_trader",
    "paper_trader",
    "sigmoid_trader",
    "executor",
    "update_pipeline_html",
    "log_oi_sample",
    ".execute(",   # no direct DB writes
    "INSERT ",
    "UPDATE ",
    "MagnetDatabase",
    "MagnetMonitor",
    "log_battle",
    "export_battles_json",
]


def _py_sources():
    return [p for p in RADAR_DIR.rglob("*.py")]


def test_radar_has_no_trader_or_db_write_references():
    offenders = []
    for path in _py_sources():
        text = path.read_text()
        for token in FORBIDDEN:
            if token in text:
                offenders.append(f"{path.name}: {token!r}")
    assert not offenders, f"radar must stay read-only; found: {offenders}"
