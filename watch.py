"""Watches Registro Civil (DGREC) booking agendas and sends a phone
notification through ntfy when appointments open up.

Usage:
    python watch.py            # check every agenda in config.json once
    python watch.py --test     # send a test notification and exit
"""

import argparse
import html as htmllib
import json
import os
import re
import ssl
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import certifi
import requests
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE = ROOT / "state.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# Phrases, without accents and in lower case, that show the page is the
# booking calendar. Note the "no hay cupos disponibles" warning is in the page
# even when slots exist (just hidden), so it can't decide open vs closed.
PAGE_MARKERS = ("agendar reserva", "seleccionar dia", "preferencia de horario", "no hay cupos disponibles")

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"

MONTEVIDEO = timezone(timedelta(hours=-3))
MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
# The first three open slots are buttons with the "btn-turno" class.
SLOT_BUTTON = re.compile(r'class="[^"]*\bbtn-turno\b[^"]*"[^>]*>\s*<span[^>]*>([^<]+)</span>', re.I)
SLOT_TEXT = re.compile(r"(\w+)\s+(\d{1,2})\s+de\s+(\w+)\s*-\s*(\d{1,2}):(\d{2})", re.U)


# ---------------------------------------------------------------- page reading

def normalize(text: str) -> str:
    """Lower case, no accents, single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def parse_slots(page: str, today: date) -> list:
    """The open slots the page shows (up to three), oldest first.
    The page has no year, so a date earlier than today means next year."""
    slots = []
    for raw in SLOT_BUTTON.findall(page):
        label = " ".join(htmllib.unescape(raw).split())
        m = SLOT_TEXT.search(label)
        if not m:
            continue
        _, day, month_name, hour, minute = m.groups()
        month = MONTHS.get(normalize(month_name))
        if not month:
            continue
        try:
            when = date(today.year, month, int(day))
            if when < today - timedelta(days=1):
                when = date(today.year + 1, month, int(day))
        except ValueError:
            continue
        slots.append({"date": when.isoformat(), "time": f"{int(hour):02d}:{minute}", "label": label})
    return slots


def read_page(html: str, today: date):
    """(status, slots): OPEN with its slots, CLOSED, or UNKNOWN when the page
    isn't the calendar we expected."""
    slots = parse_slots(html, today)
    if slots:
        return OPEN, slots
    text = normalize(html)
    if any(marker in text for marker in PAGE_MARKERS):
        return CLOSED, []
    return UNKNOWN, []


def read_status(html: str) -> str:
    return read_page(html, datetime.now(MONTEVIDEO).date())[0]


# ------------------------------------------------------------------ networking

_bundle_path = None


def _intermediate_bundle(host: str) -> str:
    """Some gub.uy servers don't send their intermediate certificates.
    Browsers fill the gap by downloading them; this does the same, then
    returns a CA file with certifi's roots plus those intermediates."""
    global _bundle_path
    if _bundle_path:
        return _bundle_path

    pem = ssl.get_server_certificate((host, 443))
    cert = x509.load_pem_x509_certificate(pem.encode())
    extra = []
    for _ in range(3):
        try:
            aia = cert.extensions.get_extension_for_oid(
                ExtensionOID.AUTHORITY_INFORMATION_ACCESS
            ).value
        except x509.ExtensionNotFound:
            break
        urls = [
            d.access_location.value
            for d in aia
            if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS
        ]
        if not urls:
            break
        data = requests.get(urls[0], timeout=20).content
        try:
            issuer = x509.load_der_x509_certificate(data)
        except ValueError:
            issuer = x509.load_pem_x509_certificate(data)
        extra.append(issuer.public_bytes(Encoding.PEM).decode())
        if issuer.issuer == issuer.subject:
            break
        cert = issuer

    bundle = Path(certifi.where()).read_text() + "\n" + "\n".join(extra)
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "w") as f:
        f.write(bundle)
    _bundle_path = path
    return path


def fetch(session: requests.Session, url: str) -> str:
    try:
        r = session.get(url, timeout=30)
    except requests.exceptions.SSLError:
        host = requests.utils.urlparse(url).hostname
        r = session.get(url, timeout=30, verify=_intermediate_bundle(host))
    r.raise_for_status()
    return r.text


# ------------------------------------------------------------------------ ntfy

def notify(title: str, message: str, priority: int = 3, tags=(), url: str = None) -> None:
    """Send a push notification to everyone subscribed to the ntfy topic.
    Priority: 1 (min) to 5 (urgent)."""
    topic = os.environ.get("NTFY_TOPIC")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    if not topic:
        print(f"NTFY_TOPIC missing, notification not sent:\n{title}\n{message}")
        return
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": list(tags),
    }
    if url:
        payload["click"] = url
        payload["actions"] = [{"action": "view", "label": "Open calendar", "url": url}]
    r = requests.post(server, json=payload, timeout=20)
    if not r.ok:
        print(f"ntfy error {r.status_code}: {r.text}")


# ----------------------------------------------------------------------- email

BREVO_API = "https://api.brevo.com/v3"


def _slot_line(slot: dict) -> str:
    when = date.fromisoformat(slot["date"])
    return f"{when.strftime('%a')} {when.day} {when.strftime('%b')}, {slot['time']}"


def build_email(openings: list) -> tuple:
    """(subject, html) for the subscribers' alert. `openings` is a list of
    (agenda, slots) for every calendar that just opened in this run."""
    subject = "Registro Civil has appointments open"
    blocks = []
    for agenda, slots in openings:
        label = htmllib.escape(agenda.get("short_name", agenda["name"]))
        items = "".join(f"<li>{htmllib.escape(_slot_line(s))}</li>" for s in slots)
        link = htmllib.escape(agenda["url"], quote=True)
        blocks.append(
            f'<p style="margin:20px 0 6px"><strong>{label}</strong></p>'
            f'<ul style="margin:0 0 8px;padding-left:20px">{items}</ul>'
            f'<p style="margin:0"><a href="{link}" style="color:#5734A0;font-weight:600">Book this calendar</a></p>'
        )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.5;color:#3a2c31;max-width:520px">'
        "<p>Hi,</p>"
        "<p>Registro Civil just opened appointments to register foreign birth certificates at Uruguay 933. "
        "These are the first open slots:</p>"
        + "".join(blocks) +
        '<p style="margin-top:24px">Spots usually go fast, so if your documents are ready '
        "(apostilled, and translated by a Uruguayan sworn translator), book as soon as you can. "
        "The booking costs $U 1.436, can\u2019t be refunded, and has to be paid within 48 hours.</p>"
        '<p>Romina<br>Uruguay Relocation Companion<br>'
        '<a href="https://relocationuy.com" style="color:#5734A0">relocationuy.com</a></p>'
        '<p style="font-size:12px;color:#5F4D53;margin-top:28px">You\u2019re getting this because you signed up '
        "for Registro Civil alerts on dnm.relocationuy.com. "
        '<a href="{{ unsubscribe }}" style="color:#5F4D53">Unsubscribe</a></p>'
        "</div>"
    )
    return subject, html


def email_subscribers(openings: list) -> None:
    """Send one alert email to the Brevo list. Does nothing until the
    BREVO_API_KEY, BREVO_LIST_ID and BREVO_SENDER_EMAIL secrets exist."""
    key = os.environ.get("BREVO_API_KEY")
    list_id = os.environ.get("BREVO_LIST_ID")
    sender = os.environ.get("BREVO_SENDER_EMAIL")
    if not (key and list_id and sender and openings):
        if openings:
            print("Brevo not configured, alert email not sent.")
        return
    subject, html = build_email(openings)
    headers = {"api-key": key, "accept": "application/json", "content-type": "application/json"}
    r = requests.post(
        f"{BREVO_API}/emailCampaigns",
        headers=headers,
        json={
            "name": f"Registro Civil alert {now()}",
            "subject": subject,
            "sender": {"name": "Uruguay Relocation Companion", "email": sender},
            "htmlContent": html,
            "recipients": {"listIds": [int(list_id)]},
        },
        timeout=30,
    )
    if not r.ok:
        print(f"Brevo campaign error {r.status_code}: {r.text}")
        return
    campaign_id = r.json()["id"]
    r = requests.post(f"{BREVO_API}/emailCampaigns/{campaign_id}/sendNow", headers=headers, timeout=30)
    print("Alert email sent." if r.ok else f"Brevo send error {r.status_code}: {r.text}")


# ----------------------------------------------------------------------- state

def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------------------ main

def check_all() -> None:
    config = load_json(CONFIG_FILE, {})
    state = load_json(STATE_FILE, {})
    fail_limit = config.get("alert_after_failures", 6)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-UY,es;q=0.9"})
    openings = []  # calendars that opened in this run, for one combined email

    for agenda in config.get("agendas", []):
        if not agenda.get("enabled", True):
            continue
        key, name, url = agenda["id"], agenda["name"], agenda["url"]
        entry = state.setdefault(key, {"status": None, "since": None, "failures": 0, "broken_alerted": False})
        entry["name"] = name
        entry["url"] = url

        try:
            status, slots = read_page(fetch(session, url), datetime.now(MONTEVIDEO).date())
            error = None
        except Exception as exc:  # network errors, HTTP errors, TLS
            status, slots, error = UNKNOWN, [], str(exc)

        print(f"[{now()}] {name}: {status}" + (f" ({error})" if error else "")
              + "".join(f"\n    {s['date']} {s['time']}" for s in slots))

        if status == UNKNOWN:
            entry["failures"] += 1
            if entry["failures"] >= fail_limit and not entry["broken_alerted"]:
                notify(
                    "Watcher can't read the page",
                    f"{name}\n{entry['failures']} checks in a row failed. Check it by hand.",
                    priority=4,
                    tags=["warning"],
                    url=url,
                )
                entry["broken_alerted"] = True
            continue

        if entry["broken_alerted"]:
            notify("Watcher is back", f"Reading {name} again.", priority=2, tags=["white_check_mark"])
        entry["failures"] = 0
        entry["broken_alerted"] = False

        # What's open now, plus the last slots ever seen open (shown by the app
        # as "last open appointments were in <month>" once they run out).
        entry["slots"] = slots
        if slots and slots != entry.get("last_slots"):
            entry["last_slots"] = slots
            entry["last_seen_open"] = now()
            months = sorted({s["date"][:7] for s in slots})
            entry["last_open_months"] = [months[0], months[-1]] if len(months) > 1 else months

        if status != entry["status"]:
            if status == OPEN:
                notify(
                    "Appointments open!",
                    f"{name}\nTap to book before they're gone.",
                    priority=5,
                    tags=["green_circle", "calendar"],
                    url=url,
                )
                openings.append((agenda, slots))
            elif entry["status"] == OPEN:
                notify("No appointments left", f"{name}\nThe calendar is full again.", priority=3, tags=["red_circle"])
            entry["status"] = status
            entry["since"] = now()

    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        email_subscribers(openings)
    except Exception as exc:  # never let email trouble stop the watcher
        print(f"Alert email failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="send a test notification")
    args = parser.parse_args()

    if args.test:
        notify("Test notification", "The URC agenda watcher can reach this phone.", tags=["wave"])
        return 0

    check_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
