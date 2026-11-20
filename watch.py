"""Watches Registro Civil (DGREC) booking agendas and sends a phone
notification through ntfy when appointments open up.

Usage:
    python watch.py            # check every agenda in config.json once
    python watch.py --test     # send a test notification and exit
"""

import argparse
import json
import os
import ssl
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
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

# Phrases, without accents and in lower case, used to read the page.
NO_SLOTS = "no hay cupos disponibles"
PAGE_MARKERS = ("agendar reserva", "seleccionar dia", "preferencia de horario", NO_SLOTS)

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"


# ---------------------------------------------------------------- page reading

def normalize(text: str) -> str:
    """Lower case, no accents, single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def read_status(html: str) -> str:
    """OPEN, CLOSED or UNKNOWN (the page wasn't the calendar we expected)."""
    text = normalize(html)
    if NO_SLOTS in text:
        return CLOSED
    if any(marker in text for marker in PAGE_MARKERS):
        return OPEN
    return UNKNOWN


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

    for agenda in config.get("agendas", []):
        if not agenda.get("enabled", True):
            continue
        key, name, url = agenda["id"], agenda["name"], agenda["url"]
        entry = state.setdefault(key, {"status": None, "since": None, "failures": 0, "broken_alerted": False})

        try:
            status = read_status(fetch(session, url))
            error = None
        except Exception as exc:  # network errors, HTTP errors, TLS
            status, error = UNKNOWN, str(exc)

        print(f"[{now()}] {name}: {status}" + (f" ({error})" if error else ""))

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

        if status != entry["status"]:
            if status == OPEN:
                notify(
                    "Appointments open!",
                    f"{name}\nTap to book before they're gone.",
                    priority=5,
                    tags=["green_circle", "calendar"],
                    url=url,
                )
            elif entry["status"] == OPEN:
                notify("No appointments left", f"{name}\nThe calendar is full again.", priority=3, tags=["red_circle"])
            entry["status"] = status
            entry["since"] = now()

    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
