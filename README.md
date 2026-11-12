# Commander Calendar

A Python web app for a small, invite-only playgroup. Calendar times default to
Europe/Bucharest. Personal events stay private. Group members see only their
busy blocks and the availability they explicitly entered.

## Try it locally

Unzip the project, open a terminal in the folder, and run with Python 3.11+:

```bash
export CALENDAR_SETUP_CODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
printf '%s\n' "$CALENDAR_SETUP_CODE"
CALENDAR_SECURE_COOKIE=0 python3 app.py
```

Open http://localhost:8000 and use the printed setup code to create the first
account. Create a group, then generate one invite code per friend. A friend
uses the code to register, or pastes it into **Join group** if they already
have an account. Invites expire after seven days and can be used once.

Mark availability in whole-hour blocks. Use **Find a time** to find
consecutive slots with the required turnout. Click a result to prefill the
game time, then enter a title and venue. Going RSVPs add the game to that
person's personal calendar. Private events and Going RSVPs block overlapping
slots when finding a new game.

## Run on your server

1. Create a virtual environment and install the production server.

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. Set a strong `CALENDAR_SETUP_CODE` for first-account setup. Set
   `CALENDAR_DB` to an absolute path in a writable, persistent directory.
   Optionally set `CALENDAR_TIMEZONE` to an IANA zone. Leave
   `CALENDAR_SECURE_COOKIE` at its default of 1.
3. Run `.venv/bin/python serve.py` as a service. It listens on
   `127.0.0.1:8000` with eight request threads. Configure your HTTPS reverse
   proxy to forward requests to that address. Use the HTTPS URL on phones.
4. Back up the database using SQLite's online backup command, for example
   `sqlite3 /path/calendar.sqlite3 ".backup /backup/calendar.sqlite3"`.
   Schedule that command using your server's backup system. Protect the
   database and backups because they contain private events and saved places.

## Arrival reminders

Enter the latitude and longitude of a place plus a radius. On your phone,
open **Arrival reminders**, tap **Start arrival checks**, and grant location
permission. The page displays the reminder on arrival and also tries a
system notification where the browser supports it. Live coordinates stay on
the device. The browser needs HTTPS outside localhost. This version checks
location while the page is active. Background detection when the phone is
locked or the page is closed requires a mobile implementation.

## First-version scope

Personal calendar views display events as a day, week, or month agenda.
The shared view is an hourly availability grid. Recurring events, recurring
availability, calendar sync, account recovery, and background mobile
geofencing are outside this build.

## Google sign-in

In Google Cloud Console, configure the OAuth consent screen, add yourself
and your friends as test users if the app is in External testing, and create
an OAuth client of type **Web application**. Set an authorized redirect URI
that matches the address you actually use, including port and path:

- Local: `http://localhost:8001/google/callback`
- Hosted: `https://calendar.example.com/google/callback`

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI`
in your shell for local testing or in the private environment file for the
hosted service. Restart the app. On NixOS, add these to
`/etc/mtg-calendar.env` with sudoedit and run
`sudo systemctl restart mtg-calendar`.

Existing users should sign in with their password, then select **Link Google
account** on their personal calendar. New users can enter a username and a
setup code (first account) or single-use group invite (subsequent accounts)
and select **Continue with Google**. On later sign-ins they can leave both
fields blank. Existing SQLite databases are upgraded when the service runs
`initialize()`.

### Set a reminder from your phone

Connect Tailscale if your calendar is private, then open its HTTPS URL.
Go to **Arrival reminders**, tap **Use my current location**, and allow
location access. Enter the reminder text and set your arrival radius,
then tap **Save reminder**. Coordinates are sent to the server only when
you save the place. **Enter or edit coordinates** lets you set a place
when you're somewhere else. The saved places are private to your account.
