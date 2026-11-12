"""Self-hosted Commander calendar. Python 3.11+."""
import base64
import hashlib
import json
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from html import escape
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from urllib.request import Request, urlopen
from wsgiref.simple_server import make_server
from zoneinfo import ZoneInfo

ZONE = ZoneInfo(os.environ.get("CALENDAR_TIMEZONE", "Europe/Bucharest"))
DB = Path(os.environ.get("CALENDAR_DB", "calendar.sqlite3"))
SECURE = os.environ.get("CALENDAR_SECURE_COOKIE", "1") == "1"
SETUP = os.environ.get("CALENDAR_SETUP_CODE", "")
GOOGLE_CLIENT = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT = os.environ.get("GOOGLE_REDIRECT_URI", "")
GOOGLE_ENABLED = bool(GOOGLE_CLIENT and GOOGLE_SECRET and GOOGLE_REDIRECT)
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE, password TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, csrf TEXT, expires INTEGER);
CREATE TABLE IF NOT EXISTS google_accounts(subject TEXT PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS google_flows(state TEXT PRIMARY KEY, token TEXT NOT NULL,
 verifier TEXT NOT NULL, name TEXT NOT NULL, code TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY, name TEXT, owner INTEGER);
CREATE TABLE IF NOT EXISTS members(group_id INTEGER, user_id INTEGER, PRIMARY KEY(group_id,user_id));
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY, group_id INTEGER, expires INTEGER);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, user_id INTEGER, group_id INTEGER,
 title TEXT, start INTEGER, end INTEGER, venue TEXT);
CREATE TABLE IF NOT EXISTS availability(group_id INTEGER,user_id INTEGER,start INTEGER,
 state TEXT, PRIMARY KEY(group_id,user_id,start));
CREATE TABLE IF NOT EXISTS rsvps(event_id INTEGER,user_id INTEGER,state TEXT,PRIMARY KEY(event_id,user_id));
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY,user_id INTEGER,label TEXT,lat REAL,lon REAL,radius INTEGER);
"""


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    with closing(connect()) as db, db:
        db.executescript(SCHEMA)


def now():
    return int(datetime.now(timezone.utc).timestamp())


def date_time(timestamp):
    return datetime.fromtimestamp(timestamp, ZONE)


def timestamp(value):
    local = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    choices = {int(local.replace(tzinfo=ZONE, fold=fold).timestamp()) for fold in (0, 1)}
    valid = [choice for choice in choices if date_time(choice).replace(tzinfo=None) == local]
    if len(valid) != 1:
        raise ValueError("Choose a time outside the daylight-saving clock change.")
    return valid[0]


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def session(db, user=None):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    db.execute("DELETE FROM sessions WHERE expires < ?", (now(),))
    db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (token, user, csrf, now()+604800))
    return token


def e(value):
    return escape(str(value), quote=True)


def form(action, csrf, fields, label):
    return (f'<form method="post" action="/{action}"><input type="hidden" name="csrf" value="{e(csrf)}">'
            f'{fields}<button>{e(label)}</button></form>')


def field(name, label, kind="text", extra=""):
    return f'<label>{e(label)}<input name="{name}" type="{kind}" {extra} required></label>'


def options(name, values):
    return f'<select aria-label="{e(name)}" name="{name}">' + "".join(
        f'<option value="{e(value)}">{e(label)}</option>' for value, label in values
    ) + '</select>'


def event_fields(group=False):
    return (field("title", "Title", extra='maxlength="100"') +
            field("start", "Starts", "datetime-local") + field("end", "Ends", "datetime-local") +
            (field("venue", "Venue", extra='maxlength="100"') if group else ""))


def page(title, body, username=None):
    signed_in = username is not None
    brand = 'Commander Calendar' if username is None else f'Commander Calendar · {username}'
    nav = '<a href="/">Calendar</a><a href="/places">Arrival reminders</a>' if signed_in else ''
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · {e(brand)}</title><link rel="stylesheet" href="/style.css">
<header><a href="/">{e(brand)}</a><nav>{nav}</nav></header>
<main><h1>{e(title)}</h1>{body}</main></html>'''


def membership(db, group_id, user_id):
    row = db.execute("SELECT g.* FROM groups g JOIN members m ON m.group_id=g.id WHERE g.id=? AND m.user_id=?",
                     (group_id, user_id)).fetchone()
    if row is None:
        raise ValueError("Group unavailable.")
    return row


def valid_invite(db, code):
    return db.execute("SELECT group_id FROM invites WHERE code=? AND expires>=?", (code, now())).fetchone()


def join_group(db, user, code):
    invite = valid_invite(db, code)
    if invite is None:
        raise ValueError("This invite is invalid, expired, or already used.")
    db.execute("INSERT OR IGNORE INTO members VALUES (?,?)", (invite["group_id"], user))
    db.execute("DELETE FROM invites WHERE code=?", (code,))
    return invite["group_id"]


def event_list(rows, csrf, user, db):
    if not rows:
        return "<p>No events in this period.</p>"
    cards = []
    for event in rows:
        dates = f'{date_time(event["start"]):%a %d %b %H:%M} to {date_time(event["end"]):%a %d %b %H:%M}'
        detail = f'<article><strong>{e(event["title"])}</strong><p>{e(dates)} · {e(event["venue"] or "Private")}</p>'
        hidden = f'<input type="hidden" name="id" value="{event["id"]}">'
        if event["group_id"] is not None:
            replies = db.execute("SELECT u.name,r.state FROM rsvps r JOIN users u ON u.id=r.user_id WHERE r.event_id=? ORDER BY u.name",
                                 (event["id"],)).fetchall()
            detail += "<p>" + e(", ".join(f'{row["name"]}: {row["state"]}' for row in replies) or "Awaiting RSVPs") + "</p>"
            detail += form("rsvp", csrf, hidden + options("state", [(s, s) for s in ("Going", "Maybe", "Declined")]), "RSVP")
        if event["user_id"] == user:
            detail += form("delete-event", csrf, hidden, "Delete event")
        cards.append(detail + "</article>")
    return "".join(cards)


def home(db, user, csrf, query, username):
    today = datetime.now(ZONE).date()
    day = datetime.strptime(query.get("date", today.isoformat()), "%Y-%m-%d").date()
    mode = query.get("view", "week")
    if mode not in {"day", "week", "month"}:
        raise ValueError("Unknown calendar view.")
    if mode == "month":
        day = day.replace(day=1)
        end_day = (day + timedelta(days=32)).replace(day=1)
    elif mode == "week":
        day -= timedelta(days=day.weekday())
        end_day = day + timedelta(days=7)
    else:
        end_day = day + timedelta(days=1)
    start = timestamp(day.isoformat() + "T00:00")
    end = timestamp(end_day.isoformat() + "T00:00")
    rows = db.execute("""SELECT e.* FROM events e LEFT JOIN rsvps r ON r.event_id=e.id AND r.user_id=?
                         WHERE ((e.group_id IS NULL AND e.user_id=?) OR r.state='Going')
                         AND e.start<? AND e.end>? ORDER BY e.start""", (user, user, end, start)).fetchall()
    groups = db.execute("SELECT g.* FROM groups g JOIN members m ON m.group_id=g.id WHERE m.user_id=? ORDER BY g.name",
                        (user,)).fetchall()
    links = "".join(f'<li><a href="/group?id={row["id"]}">{e(row["name"])}</a></li>' for row in groups)
    views = " · ".join(f'<a href="/?view={name}&date={day}">{name.title()}</a>' for name in ("day","week","month"))
    calendar = (f'<section><h2>{day:%d %b} – {end_day-timedelta(days=1):%d %b %Y}</h2>'
                f'<p>{views} · <a href="/?view={mode}&date={day-timedelta(days=1)}">Previous</a>'
                f' · <a href="/?view={mode}&date={end_day}">Next</a></p>'
                + event_list(rows, csrf, user, db) + '</section>')
    sidebar = ('<aside><section><h2>Groups</h2><ul>' + links + '</ul>' +
               form("create-group", csrf, field("name","Group name",extra='maxlength="80"'), "Create group") +
               form("join", csrf, field("code","Invite code"), "Join group") +
               '</section><section><h2>New private event</h2>' +
               form("personal-event", csrf, event_fields(), "Save event") + '</section>' +
               google_controls(csrf, user) + form("logout", csrf, "", "Sign out") + '</aside>')
    return page("Your calendar", f'<p class="muted">Times shown in {e(ZONE.key)}</p><div class="layout">{calendar}{sidebar}</div>', username)


def group_page(db, user, csrf, query, username):
    gid = int(query.get("id", 0))
    group = membership(db, gid, user)
    today = datetime.now(ZONE).date()
    day = datetime.strptime(query.get("date", today.isoformat()), "%Y-%m-%d").date()
    duration = int(query.get("hours", 3))
    minimum = int(query.get("minimum", 0))
    if not 1 <= duration <= 12 or minimum < 0:
        raise ValueError("Choose a duration from 1 to 12 hours and a valid player count.")
    start = timestamp(day.isoformat() + "T00:00")
    end = timestamp((day + timedelta(days=7)).isoformat() + "T00:00")
    people = db.execute("SELECT u.id,u.name FROM users u JOIN members m ON m.user_id=u.id WHERE m.group_id=? ORDER BY u.name", (gid,)).fetchall()
    needed = minimum or len(people)
    marks = {(r["start"],r["user_id"]):r["state"] for r in db.execute(
        "SELECT * FROM availability WHERE group_id=? AND start>=? AND start<?", (gid,start,end))}
    blocks = db.execute("""SELECT e.start,e.end,e.user_id FROM events e JOIN members m ON m.user_id=e.user_id
                           WHERE m.group_id=? AND e.group_id IS NULL AND e.start<? AND e.end>?
                           UNION ALL
                           SELECT e.start,e.end,r.user_id FROM events e JOIN rsvps r ON r.event_id=e.id
                           JOIN members m ON m.user_id=r.user_id
                           WHERE m.group_id=? AND r.state='Going' AND e.start<? AND e.end>?""",
                        (gid,end,start,gid,end,start)).fetchall()
    slots = {}
    visible = []
    for slot in range(start, end, 3600):
        states = {}
        for person in people:
            uid = person["id"]
            busy = any(b["user_id"] == uid and b["start"] < slot+3600 and b["end"] > slot for b in blocks)
            states[uid] = "Busy" if busy else marks.get((slot,uid),"No reply")
        slots[slot] = states
        if any(state != "No reply" for state in states.values()):
            visible.append(slot)

    matches = []
    for slot in slots:
        available = [p["name"] for p in people if all(
            slots.get(slot+offset*3600,{}).get(p["id"]) == "Available" for offset in range(duration))]
        if len(available) >= needed:
            matches.append((slot,available))
    matches.sort(key=lambda match: (-len(match[1]),match[0]))
    hidden = f'<input type="hidden" name="group" value="{gid}">'
    suggestions = []
    for slot, names in matches[:12]:
        stop = slot+duration*3600
        label = f"{date_time(slot):%a %d %b %H:%M}–{date_time(stop):%H:%M}"
        link = f'/group?id={gid}&date={day}&slot={slot}&hours={duration}&minimum={minimum}#new-game'
        suggestions.append(f'<li><a href="{e(link)}">{e(label)}</a> · {len(names)}/{len(people)} · {e(", ".join(names))}</li>')

    headings = "".join(f'<th>{e(person["name"])}</th>' for person in people)
    table_rows = []
    for slot in visible:
        cells = "".join(f'<td class="{e(state.lower().replace(" ","-"))}">{e(state)}</td>' for state in slots[slot].values())
        count = sum(state == "Available" for state in slots[slot].values())
        table_rows.append(f'<tr><th>{date_time(slot):%a %d %b %H:%M %Z}</th><td>{count}/{len(people)}</td>{cells}</tr>')
    table = (f'<div class="scroll"><table><thead><tr><th>Time</th><th>Available</th>{headings}</tr></thead>'
             f'<tbody>{"".join(table_rows)}</tbody></table></div>' if table_rows else "<p>No availability entered this week.</p>")
    grid = (f'<section><h2>Availability grid</h2><p>{day:%d %b} – {day+timedelta(days=6):%d %b} · {e(ZONE.key)} · Unlisted hours have no replies.</p>'
            f'<p><a href="/group?id={gid}&date={day-timedelta(days=7)}">Previous week</a> · '
            f'<a href="/group?id={gid}&date={day+timedelta(days=7)}">Next week</a></p>{table}</section>')
    search = (f'<form method="get" action="/group"><input type="hidden" name="id" value="{gid}">'
              f'<label>Week starts<input type="date" name="date" value="{day}" required></label>'
              f'<label>Hours<input name="hours" type="number" min="1" max="12" value="{duration}" required></label>'
              f'<label>Minimum players<input name="minimum" type="number" min="0" max="{len(people)}" value="{minimum}"></label>'
              '<button>Find times</button></form><p class="muted">Use 0 to require everyone.</p>')
    match_list = '<ul>' + "".join(suggestions) + '</ul>' if suggestions else '<p>No slots meet these requirements.</p>'
    mark = (hidden + field("start","From","datetime-local", 'step="3600"') +
            field("end","Until","datetime-local", 'step="3600"') +
            options("state",[(s,s) for s in ("Available","Maybe","Unavailable","Clear")]))
    invite = ""
    if group["owner"] == user:
        codes = db.execute("SELECT code FROM invites WHERE group_id=? AND expires>=?",(gid,now())).fetchall()
        invite = '<section><h2>Invites</h2>' + form("invite",csrf,hidden,"Generate single-use invite")
        invite += "".join(f'<p><code>{e(row["code"])}</code></p>' for row in codes) + '</section>'
    games = db.execute("SELECT * FROM events WHERE group_id=? AND start<? AND end>? ORDER BY start",(gid,end,start)).fetchall()
    game_fields = event_fields(True)
    if "slot" in query:
        selected = int(query["slot"])
        if selected in slots:
            for key, time in (("start",selected),("end",selected+duration*3600)):
                game_fields = game_fields.replace(f'name="{key}" type="datetime-local"',
                    f'name="{key}" type="datetime-local" value="{date_time(time):%Y-%m-%dT%H:%M}"')
    return page(group["name"], f'<div class="layout"><div>{grid}<section><h2>Find a time</h2>{search}{match_list}</section>'
                f'<section><h2>Game nights</h2>{event_list(games,csrf,user,db)}</section></div>'
                '<aside><section><h2>Your availability</h2>' + form("availability",csrf,mark,"Save availability") +
                '</section><section id="new-game"><h2>Schedule game night</h2>' +
                form("game",csrf,hidden+game_fields,"Confirm game night") + '</section>' + invite + '</aside></div>', username)


def places(db, user, csrf, username):
    rows = db.execute("SELECT * FROM reminders WHERE user_id=? ORDER BY id",(user,)).fetchall()
    entries = []
    for row in rows:
        entries.append(f'<article data-place="{row["id"]}" data-lat="{row["lat"]}" data-lon="{row["lon"]}" '
                       f'data-radius="{row["radius"]}"><strong>{e(row["label"])}</strong><p>Within {row["radius"]} m</p>' +
                       form("delete-place",csrf,f'<input type="hidden" name="id" value="{row["id"]}">',"Remove") + '</article>')
    fields = (
        '<div class="place-location">'
        '<button id="locate" type="button">Use my current location</button>'
        '<p id="location-status" role="status">Use GPS when you are at the place you want to save.</p>'
        '</div>'
        + field("label", "Reminder", extra='maxlength="120" placeholder="Bring the trade binder"')
        + '<details id="place-coordinates"><summary>Enter or edit coordinates</summary>'
        + field("lat", "Latitude", "number", 'step="any" min="-90" max="90" inputmode="decimal"')
        + field("lon", "Longitude", "number", 'step="any" min="-180" max="180" inputmode="decimal"')
        + '</details>'
        + field("radius", "Arrival radius in metres", "number", 'min="25" max="5000" value="150" inputmode="numeric"')
    )
    return page("Arrival reminders", '<p>Keep this page open on your phone while travelling. Location is checked on this device.</p>'
                '<button id="watch" type="button">Start arrival checks</button><p id="status" aria-live="polite"></p>'
                '<div class="layout place-layout"><section><h2>Add a place</h2>' + form("place",csrf,fields,"Save reminder") +
                '</section><section><h2>Your places</h2>' + ("".join(entries) or "<p>No saved places yet.</p>") +
                '</section></div><script src="/places.js" defer></script>', username)


def google_controls(csrf, user=None):
    if not GOOGLE_ENABLED:
        return ""
    fields = ""
    label = "Link Google account"
    if user is None:
        label = "Continue with Google"
        fields = ('<p>First time here? Enter a username and a setup or invite code. '
                  'Returning Google users can leave these blank.</p>'
                  '<label>Username<input name="name" maxlength="30"></label>'
                  '<label>Setup or invite code<input name="code" maxlength="120"></label>')
    return '<section><h2>Google sign-in</h2>' + form("google/start", csrf, fields, label) + "</section>"


def start_google(db, token, fields):
    if not GOOGLE_ENABLED:
        raise ValueError("Google sign-in is unavailable.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.execute("DELETE FROM google_flows WHERE expires < ? OR token = ?", (now(), token))
    db.execute("INSERT INTO google_flows VALUES (?,?,?,?,?,?)",
               (state, token, verifier, fields.get("name", "")[:30].strip().lower(),
                fields.get("code", "")[:120].strip(), now() + 600))
    query = urlencode({"client_id": GOOGLE_CLIENT, "redirect_uri": GOOGLE_REDIRECT,
                       "response_type": "code", "scope": "openid", "state": state,
                       "code_challenge": challenge, "code_challenge_method": "S256",
                       "prompt": "select_account"})
    return "https://accounts.google.com/o/oauth2/v2/auth?" + query


def google_subject(code, verifier):
    try:
        data = urlencode({"client_id": GOOGLE_CLIENT, "client_secret": GOOGLE_SECRET,
                          "redirect_uri": GOOGLE_REDIRECT, "code": code,
                          "code_verifier": verifier, "grant_type": "authorization_code"}).encode()
        with urlopen(Request("https://oauth2.googleapis.com/token", data=data), timeout=10) as response:
            access = json.load(response)["access_token"]
        request = Request("https://openidconnect.googleapis.com/v1/userinfo",
                          headers={"Authorization": "Bearer " + access})
        with urlopen(request, timeout=10) as response:
            subject = json.load(response)["sub"]
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise ValueError("Invalid Google account.")
        return subject
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise ValueError("Google sign-in could not be completed. Start again from the calendar.") from error


def finish_google(db, token, user, query):
    if not GOOGLE_ENABLED or not token:
        raise ValueError("Start Google sign-in from the calendar.")
    db.execute("BEGIN IMMEDIATE")
    flow = db.execute("DELETE FROM google_flows WHERE state=? AND token=? AND expires>=? RETURNING *",
                      (query.get("state", ""), token, now())).fetchone()
    db.commit()
    if flow is None or not query.get("code"):
        raise ValueError("Google sign-in expired or was cancelled. Start again from the calendar.")
    subject = google_subject(query["code"], flow["verifier"])
    db.execute("BEGIN IMMEDIATE")
    active = db.execute("SELECT 1 FROM sessions WHERE token=? AND expires>=?", (token, now())).fetchone()
    if active is None:
        raise ValueError("Your session expired. Start Google sign-in again.")
    linked = db.execute("SELECT user_id FROM google_accounts WHERE subject=?", (subject,)).fetchone()
    destination = "/"
    if linked:
        if user is not None and linked["user_id"] != user:
            raise ValueError("That Google account is already linked to another calendar account.")
        user = linked["user_id"]
    elif user is not None:
        if db.execute("SELECT 1 FROM google_accounts WHERE user_id=?", (user,)).fetchone():
            raise ValueError("Your calendar account already has a linked Google account.")
        db.execute("INSERT INTO google_accounts VALUES (?,?)", (subject, user))
    else:
        name, code = flow["name"], flow["code"]
        if not re.fullmatch(r"[a-z0-9_.-]{3,30}", name):
            raise ValueError("For your first Google sign-in, enter a username and setup or invite code.")
        bootstrap = not db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        if bootstrap:
            if not SETUP or not hmac.compare_digest(code.encode(), SETUP.encode()):
                raise ValueError("Enter the server's setup code.")
        elif not valid_invite(db, code):
            raise ValueError("A valid group invite is required.")
        user = db.execute("INSERT INTO users(name,password) VALUES (?,NULL)", (name,)).lastrowid
        db.execute("INSERT INTO google_accounts VALUES (?,?)", (subject, user))
        if not bootstrap:
            destination = f"/group?id={join_group(db, user, code)}"
    db.execute("DELETE FROM sessions WHERE token=?", (token,))
    return destination, session(db, user)


def required(fields, key, limit=120):
    value = fields.get(key, "").strip()
    if not value or len(value) > limit:
        raise ValueError(f"Enter a valid {key}.")
    return value


def mutate(db, route, user, token, fields):
    def group_url(gid):
        return f"/group?id={gid}"

    if route in {"login", "register"}:
        name = required(fields, "name", 30)
        password = fields.get("password", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", name) or not 12 <= len(password) <= 128:
            raise ValueError("Use a 3–30 character username and a 12–128 character password.")
        name = name.lower()
        if route == "register":
            code = required(fields,"code")
            bootstrap = not db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            if bootstrap and (not SETUP or not hmac.compare_digest(code.encode(),SETUP.encode())):
                raise ValueError("Enter the server's setup code.")
            if not bootstrap and not valid_invite(db,code):
                raise ValueError("A valid group invite is required.")
            salt = secrets.token_hex(16)
            user = db.execute("INSERT INTO users(name,password) VALUES (?,?)",
                              (name,salt+":"+password_hash(password,salt))).lastrowid
            destination = "/" if bootstrap else group_url(join_group(db,user,code))
        else:
            row = db.execute("SELECT * FROM users WHERE name=?",(name,)).fetchone()
            salt, stored = row["password"].split(":") if row and row["password"] else ("0"*32,"0"*128)
            if not hmac.compare_digest(password_hash(password,salt),stored) or row is None:
                raise ValueError("Invalid username or password.")
            user, destination = row["id"], "/"
        db.execute("DELETE FROM sessions WHERE token=?",(token,))
        return destination, session(db,user)
    if not user:
        raise ValueError("Sign in first.")
    if route == "logout":
        db.execute("DELETE FROM sessions WHERE token=?",(token,))
        return "/", session(db)
    if route == "create-group":
        gid = db.execute("INSERT INTO groups(name,owner) VALUES (?,?)",(required(fields,"name",80),user)).lastrowid
        db.execute("INSERT INTO members VALUES (?,?)",(gid,user))
        return group_url(gid), None
    if route == "join":
        return group_url(join_group(db,user,required(fields,"code"))), None
    if route in {"invite","availability","game"}:
        gid = int(required(fields,"group"))
        group = membership(db,gid,user)
        if route == "invite":
            if group["owner"] != user:
                raise ValueError("Only the group owner can generate invites.")
            db.execute("INSERT INTO invites VALUES (?,?,?)",(secrets.token_urlsafe(18),gid,now()+604800))
            return group_url(gid), None
    else:
        gid = None
    if route in {"availability","game","personal-event"}:
        start,end = (timestamp(required(fields,key)) for key in ("start","end"))
        if not start < end or end-start > 31*86400:
            raise ValueError("End must follow start. The maximum range is 31 days.")
        if route == "availability":
            state = required(fields,"state")
            if state not in {"Available","Maybe","Unavailable","Clear"} or any(date_time(t).minute for t in (start,end)):
                raise ValueError("Choose a status and use whole-hour boundaries.")
            if state == "Clear":
                db.execute("DELETE FROM availability WHERE group_id=? AND user_id=? AND start>=? AND start<?",
                           (gid,user,start,end))
            else:
                db.executemany("INSERT OR REPLACE INTO availability VALUES (?,?,?,?)",
                               ((gid,user,slot,state) for slot in range(start,end,3600)))
        else:
            venue = required(fields,"venue",100) if gid else ""
            event = db.execute("INSERT INTO events(user_id,group_id,title,start,end,venue) VALUES (?,?,?,?,?,?)",
                               (user,gid,required(fields,"title",100),start,end,venue)).lastrowid
            if gid:
                db.execute("INSERT INTO rsvps VALUES (?,?,?)",(event,user,"Going"))
        return group_url(gid)+f"&date={date_time(start):%Y-%m-%d}" if gid else f"/?date={date_time(start):%Y-%m-%d}", None
    if route in {"rsvp","delete-event"}:
        event = db.execute("SELECT * FROM events WHERE id=?",(int(required(fields,"id")),)).fetchone()
        if event is None:
            raise ValueError("Event unavailable.")
        gid = event["group_id"]
        if gid:
            membership(db,gid,user)
        if route == "delete-event":
            if event["user_id"] != user:
                raise ValueError("Event unavailable.")
            db.execute("DELETE FROM rsvps WHERE event_id=?",(event["id"],))
            db.execute("DELETE FROM events WHERE id=?",(event["id"],))
        else:
            state = required(fields,"state")
            if not gid or state not in {"Going","Maybe","Declined"}:
                raise ValueError("Invalid RSVP.")
            db.execute("INSERT OR REPLACE INTO rsvps VALUES (?,?,?)",(event["id"],user,state))
        return group_url(gid)+f'&date={date_time(event["start"]):%Y-%m-%d}' if gid else "/", None
    if route == "place":
        lat,lon = (float(required(fields,k)) for k in ("lat","lon"))
        radius = int(required(fields,"radius"))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 25 <= radius <= 5000):
            raise ValueError("Check the coordinates and radius.")
        db.execute("INSERT INTO reminders(user_id,label,lat,lon,radius) VALUES (?,?,?,?,?)",
                   (user,required(fields,"label"),lat,lon,radius))
        return "/places", None
    if route == "delete-place":
        db.execute("DELETE FROM reminders WHERE id=? AND user_id=?",(int(required(fields,"id")),user))
        return "/places", None
    raise ValueError("Unknown action.")


def params(raw):
    return {key: values[-1] for key, values in parse_qs(raw, max_num_fields=30).items()}


def application(environ, start_response):
    route = environ.get("PATH_INFO","/").strip("/")
    method = environ["REQUEST_METHOD"]
    headers = [
        ("Cache-Control","no-store"),
        ("X-Content-Type-Options","nosniff"),
        ("Referrer-Policy","no-referrer"),
        ("Permissions-Policy","geolocation=(self)"),
        ("Content-Security-Policy","default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self' https://accounts.google.com https://accounts.google.com"),
    ]

    def send(status, body="", kind="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        start_response(status,headers+[("Content-Type",kind),("Content-Length",str(len(data)))])
        return [data]

    def cookie(token):
        value = f"calendar={token}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax"
        headers.append(("Set-Cookie",value+("; Secure" if SECURE else "")))

    if method not in {"GET","POST"}:
        return send("405 Method Not Allowed")
    if method == "GET" and route in {"style.css","places.js"}:
        kind = "text/css" if route.endswith("css") else "text/javascript"
        return send("200 OK",Path(__file__).with_name(route).read_text(),kind+"; charset=utf-8")
    try:
        jar = SimpleCookie(environ.get("HTTP_COOKIE",""))
        token = jar["calendar"].value if "calendar" in jar else ""
        with closing(connect()) as db, db:
            if method == "POST":
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE token=? AND expires>=?",(token,now())).fetchone() if token else None
            user = row["user_id"] if row else None
            if method == "POST":
                size = int(environ.get("CONTENT_LENGTH") or 0)
                if not 0 < size <= 16384 or not environ.get("CONTENT_TYPE","").startswith("application/x-www-form-urlencoded"):
                    return send("400 Bad Request",page("Invalid request",'<a href="/">Return to calendar</a>'))
                fields = params(environ["wsgi.input"].read(size).decode())
                if not row or not hmac.compare_digest(fields.get("csrf","").encode(),row["csrf"].encode()):
                    return send("403 Forbidden",page("Session check failed",'<a href="/">Open the calendar again</a>'))
                if route == "google/start":
                    destination, new_token = start_google(db, token, fields), None
                else:
                    destination,new_token = mutate(db,route,user,token,fields)
                db.commit()
                if new_token:
                    cookie(new_token)
                headers.append(("Location",destination))
                return send("303 See Other")
            if route == "google/callback":
                destination, new_token = finish_google(db, token if row else "", user, params(environ.get("QUERY_STRING", "")))
                db.commit()
                cookie(new_token)
                headers.append(("Location", destination))
                return send("303 See Other")
            if not user:
                if row is None:
                    token = session(db)
                    db.commit()
                    cookie(token)
                    row = db.execute("SELECT * FROM sessions WHERE token=?",(token,)).fetchone()
                csrf = row["csrf"]
                fields = field("name","Username") + field("password","Password","password",'minlength="12" maxlength="128"')
                body = ('<div class="layout"><section><h2>Sign in</h2>' + form("login",csrf,fields,"Sign in") +
                        '</section><section><h2>Create an account</h2>' +
                        form("register",csrf,fields+field("code","Setup or invite code"),"Create account") + '</section></div>')
                return send("200 OK",page("Plan your next game", google_controls(csrf) + body))
            username = db.execute("SELECT name FROM users WHERE id=?", (user,)).fetchone()["name"]
            query = params(environ.get("QUERY_STRING",""))
            if route == "":
                body = home(db,user,row["csrf"],query,username)
            elif route == "group":
                body = group_page(db,user,row["csrf"],query,username)
            elif route == "places":
                body = places(db,user,row["csrf"],username)
            else:
                return send("404 Not Found",page("Page unavailable",'<a href="/">Return to calendar</a>',username))
            return send("200 OK",body)
    except (ValueError,OverflowError,sqlite3.IntegrityError) as error:
        message = "That username is already in use." if isinstance(error,sqlite3.IntegrityError) else str(error)
        return send("400 Bad Request",page("Check your request",f"<p>{e(message)}</p>"+'<a href="/">Return to calendar</a>'))


if __name__ == "__main__":
    initialize()
    with make_server("127.0.0.1",8000,application) as server:
        print("Open http://localhost:8000")
        server.serve_forever()
