# Commander Calendar

A self-hosted calendar for organizing Magic: The Gathering game nights.
Each player has their own account, calendar, and availability.

## Using the app

- **Accounts and groups.** Create the first account using the deployment setup
  code. Create a group and generate one single-use invite per friend. New users
  supply an invite during signup. Existing users use **Join group**. With Google
  configured, returning users can click **Continue with Google** without entering
  a username or invite again. Existing password users can link Google after signing in.
- **Personal availability.** Under **Your calendar → My availability**, tap the
  first and last hourly tiles in a block, select **Available**, **Maybe**,
  **Unavailable**, or **Clear**, then tap **Add block**. Repeat across the week
  and select **Save week** once. For 12:00–16:00 and 18:00–22:00, select
  12:00 through 15:00 and 18:00 through 21:00 as two blocks.
- **Group availability.** Select a group and date range under **Import into a
  group**. Import copies your saved hours and replaces your group status for
  those hours. Later personal edits are private until imported again.
- **Schedule a game.** The group calendar shows overlap and highlights viable
  start times for the full session. Choose **Everyone available** or a minimum
  player count, then select a match to prefill game time. Players RSVP to the
  scheduled game. Going RSVPs appear on their calendars.
- **Clear availability.** **Clear this week** clears personal availability for
  that week. **Reset my calendar inputs** clears your personal and group
  availability and private events. Shared games, RSVPs and saved places are kept.
- **Arrival reminders.** On your phone, open the HTTPS site and use **Use my
  current location** to save a place. Set a radius and tap **Start arrival
  checks**. The page must stay active for arrival checks.

## Run from source

Requires Python 3.11 or newer.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
CALENDAR_SETUP_CODE=local-test CALENDAR_SECURE_COOKIE=0 CALENDAR_PORT=8001 .venv/bin/python serve.py
```

Open http://localhost:8001 and use `local-test` for first-account signup
on this local development instance.

## Deploy to Ubuntu with Portainer

Use a Docker Standalone environment in Portainer. Commit the app and Docker
files to your GitHub repository. In **Stacks → Add stack → Repository**,
enter the repository URL, reference `refs/heads/main` (or your actual
branch), and Compose path `compose.yaml`. Deploy only one instance on this
host. The stack builds the image and binds it to host loopback on port 8001.

Set the environment variables from `.env.example` in Portainer.
For a fresh install generate a setup code with
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
For a migrated database use your existing account. Set the three Google
variables together to enable Google login. Keep secure cookies enabled for HTTPS.

If deploying through a shell instead, copy `.env.example` to `.env`, edit
it on the server, and run `docker compose up -d --build` from the checkout.
For future releases, pull the updated repository and rebuild with the same
command, or redeploy the Git stack in Portainer. The named volume persists
across container replacements. Keep your real environment settings private.

### Tailscale and Google sign-in

On the Ubuntu host with Tailscale already connected:

```sh
sudo tailscale serve --bg http://127.0.0.1:8001
tailscale serve status
curl -fsS http://127.0.0.1:8001/ >/dev/null
```

Use the host port you configured if different. Open the HTTPS URL from
`tailscale serve status` on a device connected to Tailscale. Add that URL
plus `/google/callback` to **Authorized redirect URIs** in your existing
Google Web OAuth client, and use the exact same value for
`GOOGLE_REDIRECT_URI`. Reuse the existing Google client ID and secret.
If the OAuth app is in Testing, add your friends as test users.
Share the Ubuntu machine in Tailscale and give each friend a calendar invite.

For a public domain instead, terminate HTTPS on a reverse proxy on the
Ubuntu host and proxy to the same loopback port. Update the Google callback
to match that domain.

## Move your data to the new server

Stop taking changes on the old calendar before making the final snapshot.
From the old app directory, use Python's SQLite online backup API. If your
database was configured elsewhere, substitute its actual path.

```sh
umask 077
python3 - <<'PY'
import sqlite3

with sqlite3.connect("file:calendar.sqlite3?mode=ro", uri=True) as source:
    with sqlite3.connect("calendar-migration.sqlite3") as backup:
        source.backup(backup)
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
PY
scp calendar-migration.sqlite3 ubuntu-user@SERVER:~/
```

Before the first deployment on Ubuntu, import the snapshot into the volume.
Run the following from the Ubuntu account that received the file. If Docker
requires it, prefix the Docker commands with `sudo`.

```sh
docker volume create commander-calendar-data
docker run --rm -i -v commander-calendar-data:/data python:3.13-slim \
  sh -ec 'test ! -e /data/calendar.sqlite3; umask 077; cat > /data/calendar.sqlite3; chown 10001:10001 /data /data/calendar.sqlite3' \
  < "$HOME/calendar-migration.sqlite3"
```

The import refuses to overwrite an existing database. If you already deployed
with a fresh database, stop the stack and back up that database before replacing
it. Deploy the stack and verify your existing login, groups, and availability.
Keep the original instance and snapshot until you verify the migration.

For ongoing backups use SQLite's online backup API, or stop the container
before copying its database. Store snapshots outside the container volume.
