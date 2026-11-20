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

# Same structure as a real SAE page with openings: the "no hay cupos" warning
# is still in the HTML, just hidden, and the slots are btn-turno buttons.
CALENDAR_OPEN = """
<li>Agendar reserva</li>
<div id="form:warnSinCupos" style="display:none"><h4>En la oficina seleccionada no hay cupos disponibles</h4></div>
<span class="label control-label">Turnos disponibles</span>
<div id="primeros-turnos"><button id="form:j_idt72:0:j_idt73" class="ui-button btn-lg btn-turno turno-seleccionado" type="submit"><span class="ui-button-text ui-c">Mi&eacute;rcoles 2 de diciembre - 10:00 h</span></button><button class="ui-button btn-lg btn-turno" type="submit"><span class="ui-button-text ui-c">Jueves 3 de diciembre - 9:30 h</span></button><button class="ui-button btn-lg btn-turno" type="submit"><span class="ui-button-text ui-c">Martes 12 de enero - 11:00 h</span></button></div>
"""

ERROR_PAGE = "<html><body>Servicio no disponible</body></html>"

# What the real a=84 page says when it's full (Nov 2026).
REAL_FULL = """
<h1>Inscripción de partidas extranjeras titulares o representantes</h1>
<li>Agendar reserva</li><li>Datos necesarios</li>
<span>En la oficina seleccionada no hay cupos disponibles</span>
<span>A la brevedad se añadirán cupos</span>
"""

ACCESS_DENIED = """
<h2>Acceso denegado</h2>
<p>No tiene acceso al recurso solicitado o éste no existe</p>
"""


def test_real_full_page_is_closed():
    assert watch.read_status(REAL_FULL) == watch.CLOSED


def test_access_denied_is_unknown():
    assert watch.read_status(ACCESS_DENIED) == watch.UNKNOWN


def test_full_calendar_is_closed():
    assert watch.read_status(CALENDAR_FULL) == watch.CLOSED


def test_banner_matches_without_accents_or_case():
    assert watch.read_status("NO HAY CUPOS   DISPONIBLES") == watch.CLOSED


def test_calendar_without_banner_is_open():
    assert watch.read_status(CALENDAR_OPEN) == watch.OPEN


def test_unrelated_page_is_unknown():
    assert watch.read_status(ERROR_PAGE) == watch.UNKNOWN


def _run(tmp_path, monkeypatch, pages):
    """Run check_all once per page in `pages`, collecting notification titles."""
    config = {
        "alert_after_failures": 2,
        "agendas": [{"id": "a", "name": "Agenda A", "url": "https://example.test"}],
    }
    (tmp_path / "config.json").write_text(json.dumps(config))
    monkeypatch.setattr(watch, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(watch, "STATE_FILE", tmp_path / "state.json")

    sent = []
    monkeypatch.setattr(watch, "notify", lambda title, message, **kw: sent.append((title, kw.get("priority", 3))))
    for page in pages:
        monkeypatch.setattr(watch, "fetch", lambda s, u, p=page: p)
        watch.check_all()
    return sent


def test_alerts_once_when_slots_open(tmp_path, monkeypatch):
    sent = _run(tmp_path, monkeypatch, [CALENDAR_FULL, CALENDAR_OPEN, CALENDAR_OPEN])
    assert sent == [("Appointments open!", 5)]


def test_tells_you_when_slots_are_gone(tmp_path, monkeypatch):
    sent = _run(tmp_path, monkeypatch, [CALENDAR_FULL, CALENDAR_OPEN, CALENDAR_FULL])
    assert [t for t, _ in sent] == ["Appointments open!", "No appointments left"]


def test_warns_once_when_page_breaks_then_recovers(tmp_path, monkeypatch):
    sent = _run(tmp_path, monkeypatch, [CALENDAR_FULL, ERROR_PAGE, ERROR_PAGE, ERROR_PAGE, CALENDAR_FULL])
    assert [t for t, _ in sent] == ["Watcher can't read the page", "Watcher is back"]


def test_notify_sends_ntfy_json(monkeypatch):
    calls = []

    class Resp:
        ok = True

    monkeypatch.setenv("NTFY_TOPIC", "urc-test-topic")
    monkeypatch.delenv("NTFY_SERVER", raising=False)
    monkeypatch.setattr(watch.requests, "post", lambda url, json, timeout: calls.append((url, json)) or Resp())
    watch.notify("Appointments open!", "Agenda A", priority=5, url="https://example.test")

    url, body = calls[0]
    assert url == "https://ntfy.sh"
    assert body["topic"] == "urc-test-topic" and body["priority"] == 5
    assert body["click"] == "https://example.test"
    assert body["actions"][0]["url"] == "https://example.test"


def test_open_page_with_hidden_warning_reads_slots():
    from datetime import date
    status, slots = watch.read_page(CALENDAR_OPEN, date(2026, 11, 20))
    assert status == watch.OPEN
    assert [(s["date"], s["time"]) for s in slots] == [
        ("2026-12-02", "10:00"), ("2026-12-03", "09:30"), ("2027-01-12", "11:00")
    ]


def test_state_keeps_last_open_slots_after_closing(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch, [CALENDAR_FULL, CALENDAR_OPEN, CALENDAR_FULL])
    entry = json.loads((tmp_path / "state.json").read_text())["a"]
    assert entry["status"] == "closed"
    assert entry["slots"] == []
    assert entry["last_slots"][0]["time"] == "10:00"
    assert entry["last_seen_open"]
    assert entry["last_open_months"] == ["2026-12", "2027-01"]
    assert entry["name"] == "Agenda A"


def test_alert_email_lists_slots_and_unsubscribe():
    agenda = {"name": "X", "short_name": "Holder only", "url": "https://example.test/a?e=9&a=82"}
    slots = [{"date": "2026-12-02", "time": "10:00"}, {"date": "2027-01-12", "time": "11:00"}]
    subject, html = watch.build_email([(agenda, slots)])
    assert "Registro Civil" in subject
    assert "Wed 2 Dec, 10:00" in html and "Tue 12 Jan, 11:00" in html
    assert "{{ unsubscribe }}" in html
    assert "e=9&amp;a=82" in html


def test_email_skipped_without_brevo_keys(monkeypatch, capsys):
    for k in ("BREVO_API_KEY", "BREVO_LIST_ID", "BREVO_SENDER_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(watch.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    watch.email_subscribers([({"name": "X", "url": "u"}, [{"date": "2026-12-02", "time": "10:00"}])])
    assert "not configured" in capsys.readouterr().out


def test_email_creates_and_sends_campaign(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "k")
    monkeypatch.setenv("BREVO_LIST_ID", "7")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "contact@relocationuy.com")
    calls = []

    class Resp:
        def __init__(self, body=None):
            self.ok, self._body, self.status_code, self.text = True, body, 200, ""

        def json(self):
            return self._body

    def post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return Resp({"id": 42}) if url.endswith("/emailCampaigns") else Resp()

    monkeypatch.setattr(watch.requests, "post", post)
    watch.email_subscribers([({"name": "X", "url": "u"}, [{"date": "2026-12-02", "time": "10:00"}])])
    assert calls[0][1]["recipients"] == {"listIds": [7]}
    assert calls[1][0].endswith("/emailCampaigns/42/sendNow")
