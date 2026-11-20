# URC schedule tracking

Checks the Registro Civil booking calendars every 10 minutes and sends a Telegram message the moment appointments open up, so nobody has to refresh the page by hand.

Currently watching the agendas for **inscripción de partidas extranjeras** (registering foreign birth certificates at Uruguay 933):

| Agenda | Who books it |
|---|---|
| `a=84` | The certificate holder or a representative (parents booking for their kids) |
| `a=82` | The certificate holder only (can add spouse and kids to the same appointment) |

## What you get on Telegram

- **Appointments open!** with the booking link, once per opening
- **No appointments left again** when they're gone
- **Watcher can't read the page** if six checks in a row fail (site down, page changed), plus a message when it recovers

## Setup

1. **Make the bot.** In Telegram, message `@BotFather`, send `/newbot`, pick a name. Copy the token it gives you.
2. **Get your chat ID.** Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number under `"chat":{"id": ...}`.
   To get alerts in a group instead, add the bot to the group, send a message there, and use that chat's ID (it starts with `-`).
3. **Add the secrets.** In this repo: Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. **Test it.** Actions → Watch agendas → Run workflow → tick *Only send a test Telegram message*. You should get a hello message.
5. Run it once more without the tick to do a real check. After that it runs by itself.

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
python watch.py --test   # test message
python -m pytest tests
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as environment variables first, otherwise messages are just printed.
