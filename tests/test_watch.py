import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watch  # noqa: E402

CALENDAR_FULL = """
<div>Preferencia de horario: Cualquier horario</div>
<div>Seleccionar día: Noviembre 2026</div>
<span class="error">No hay cupos disponibles</span>
"""

CALENDAR_OPEN = """
<div>Preferencia de horario: Cualquier horario</div>
<div>Seleccionar día: Diciembre 2026</div>
<td class="dia-disponible">14</td>
"""

ERROR_PAGE = "<html><body>Servicio no disponible</body></html>"


def test_full_calendar_is_closed():
    assert watch.read_status(CALENDAR_FULL) == watch.CLOSED


def test_banner_matches_without_accents_or_case():
    assert watch.read_status("NO HAY CUPOS   DISPONIBLES") == watch.CLOSED


def test_calendar_without_banner_is_open():
    assert watch.read_status(CALENDAR_OPEN) == watch.OPEN


def test_unrelated_page_is_unknown():
    assert watch.read_status(ERROR_PAGE) == watch.UNKNOWN


def _run(tmp_path, monkeypatch, pages):
    """Run check_all once per page in `pages`, collecting Telegram messages."""
    config = {
        "alert_after_failures": 2,
        "agendas": [{"id": "a", "name": "Agenda A", "url": "https://example.test"}],
    }
    (tmp_path / "config.json").write_text(json.dumps(config))
    monkeypatch.setattr(watch, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(watch, "STATE_FILE", tmp_path / "state.json")

    sent = []
    monkeypatch.setattr(watch, "send_telegram", sent.append)
    for page in pages:
        monkeypatch.setattr(watch, "fetch", lambda s, u, p=page: p)
        watch.check_all()
    return sent


def test_alerts_once_when_slots_open(tmp_path, monkeypatch):
    sent = _run(tmp_path, monkeypatch, [CALENDAR_FULL, CALENDAR_OPEN, CALENDAR_OPEN])
    assert len(sent) == 1 and "open" in sent[0].lower()


def test_tells_you_when_slots_are_gone(tmp_path, monkeypatch):
    sent = _run(tmp_path, monkeypatch, [CALENDAR_FULL, CALENDAR_OPEN, CALENDAR_FULL])
    assert len(sent) == 2 and "no appointments left" in sent[1]


def test_warns_once_when_page_breaks_then_recovers(tmp_path, monkeypatch):
    sent = _run(tmp_path, monkeypatch, [CALENDAR_FULL, ERROR_PAGE, ERROR_PAGE, ERROR_PAGE, CALENDAR_FULL])
    assert len(sent) == 2
    assert "can't read" in sent[0]
    assert "again" in sent[1]
