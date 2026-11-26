# URC schedule tracking

Checks the Registro Civil booking calendars every 10 minutes and sends a phone notification (through [ntfy](https://ntfy.sh)) the moment appointments open up, so nobody has to refresh the page by hand.

Currently watching the agendas for **inscripción de partidas extranjeras** (registering foreign birth certificates at Uruguay 933):

| Agenda | Who books it |
|---|---|
| `a=84` | The certificate holder or a representative (parents booking for their kids) |
| `a=82` | The certificate holder only (can add spouse and kids to the same appointment) |

## What you get

- **Appointments open!** (urgent priority) with a button that opens the calendar, once per opening
- **No appointments left** when they're gone
- **Watcher can't read the page** if six checks in a row fail (site down, page changed), plus a quiet message when it recovers

Everyone subscribed to the topic gets the same notifications, on Android or iPhone.

## Setup

1. **Pick a topic name.** Anything long and hard to guess, like `urc-agenda-k7q2m9xv4p`. Anyone who knows the name can read the alerts, so don't make it obvious.
2. **Add it as a secret.** In this repo: Settings → Secrets and variables → Actions → New repository secret, name `NTFY_TOPIC`, value your topic name.
3. **Subscribe on each phone.** Install **ntfy** (Play Store or App Store), tap **+**, type the topic name, keep the default server (`ntfy.sh`), subscribe.
4. **Make the opening alert loud.** In the app, open the topic → settings → set notifications to make sound/vibrate even for urgent alerts, and on Android allow ntfy to ignore battery optimization so alerts arrive instantly.
5. **Test it.** Actions → Watch agendas → Run workflow → tick *Only send a test notification*. Every subscribed phone should buzz.
6. Run it once more without the tick to do a real check. After that it runs by itself.

Self-hosting ntfy is optional: add an `NTFY_SERVER` secret with your server's URL and subscribe to that server in the app instead.

## Email alerts (Brevo)

When a calendar opens, the watcher also emails everyone on a Brevo list (people who signed up on the Registro Civil tab of dnm.relocationuy.com). One email per opening, listing the first slots of every calendar that opened, with an unsubscribe link.

It stays off until these repo secrets exist:

- `BREVO_API_KEY`: Brevo → SMTP & API → API keys
- `BREVO_LIST_ID`: the number of the "Registro Civil alerts" list
- `BREVO_SENDER_EMAIL`: a verified Brevo sender, e.g. contact@relocationuy.com

## Discord alerts (URC Community)

When a calendar opens, the watcher also posts in #appointment-alerts on the URC Community server, with the first slots and a link to the calendar, and posts again when it fills up. The test run (*Only send a test notification*) posts a test message there too.

It stays off until these repo secrets exist:

- `DISCORD_WEBHOOK_URL`: Discord → Server Settings → Integrations → Webhooks → the "URC Appointment Alerts" webhook → Copy Webhook URL
- `DISCORD_PING_ROLE_ID` (optional): the Client role's ID, so openings ping clients. Without it the alert posts quietly.

## Adding or pausing agendas

Everything lives in `config.json`. Any public calendar on `sae.mec.gub.uy` (or other gub.uy agendas built on the same system) works: copy the calendar page URL, add an entry, done. Set `"enabled": false` to pause one without deleting it.

## Notes

- GitHub's schedule isn't exact. Checks can run a few minutes late when GitHub is busy.
- `state.json` is how the watcher remembers what it already told you. It only gets a new commit when something changes.
- On public repos, GitHub switches off scheduled workflows after 60 days with no activity. If alerts stop, check the Actions tab and re-enable it.
- To stop everything: Actions → Watch agendas → ⋯ → Disable workflow.

## Running it locally

```bash
pip install -r requirements.txt
python watch.py          # one check
python watch.py --test   # test notification
python -m pytest tests
```

Set `NTFY_TOPIC` as an environment variable first, otherwise notifications are just printed.
