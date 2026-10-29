"""Password-protected local management panel for welcome and suggestion data."""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import time
from collections import defaultdict, deque
from string import Formatter
from urllib.parse import urlencode

from aiohttp import web

import config
import database

COOKIE_NAME = "nov_web_session"
SESSION_TTL = 8 * 60 * 60
SESSION_SECRET = secrets.token_bytes(32)
LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
ALLOWED_STATUSES = {"pending", "approved", "denied", "implemented"}
ALLOWED_PLACEHOLDERS = {"mention", "server", "member_count", "username"}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _session_cookie(nonce: str, expires_at: int) -> str:
    payload = f"{expires_at}.{nonce}"
    signature = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _read_session(value: str | None) -> tuple[str, int] | None:
    if not value:
        return None
    try:
        expires_text, nonce, signature = value.split(".", 2)
        expires_at = int(expires_text)
    except (ValueError, TypeError):
        return None
    payload = f"{expires_at}.{nonce}"
    expected = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    if expires_at < int(time.time()) or not hmac.compare_digest(signature, expected):
        return None
    return nonce, expires_at


def _csrf_for(nonce: str) -> str:
    return hmac.new(
        SESSION_SECRET, f"csrf:{nonce}".encode(), hashlib.sha256
    ).hexdigest()


@web.middleware
async def security_middleware(request: web.Request, handler):
    if request.path not in {"/login", "/healthz"}:
        session = _read_session(request.cookies.get(COOKIE_NAME))
        if session is None:
            raise web.HTTPSeeOther("/login")
        request["session_nonce"] = session[0]
        request["csrf"] = _csrf_for(session[0])

    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _check_csrf(request: web.Request, form) -> None:
    origin = request.headers.get("Origin")
    expected_origin = config.WEBUI_PUBLIC_ORIGIN or f"{request.scheme}://{request.host}"
    if origin and origin.rstrip("/") != expected_origin:
        raise web.HTTPForbidden(text="Request origin was rejected.")
    supplied = form.get("csrf", "")
    if not hmac.compare_digest(str(supplied), request["csrf"]):
        raise web.HTTPForbidden(text="Your session expired. Reload the page and try again.")


def _valid_template(value: str) -> bool:
    try:
        for _, field_name, format_spec, conversion in Formatter().parse(value):
            if field_name is not None and (
                field_name not in ALLOWED_PLACEHOLDERS or format_spec or conversion
            ):
                return False
    except ValueError:
        return False
    return True


def _login_html(error: str = "") -> str:
    error_html = f'<p class="error">{_escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Nov Bot · Sign in</title><style>{_CSS}</style></head>
<body><main class="login"><div class="brand"><span class="mark">N</span><div><b>Nov Bot</b><small>Management</small></div></div>
<h1>Sign in</h1><p class="muted">Enter the management password configured for this bot.</p>{error_html}
<form method="post" action="/login"><label>Password<input name="password" type="password" autocomplete="current-password" required autofocus></label>
<button type="submit">Continue</button></form></main></body></html>"""


_CSS = """
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--line:#27334b;--text:#edf2ff;--muted:#9ba9c4;--accent:#91a8ff;--green:#64d7b0;--red:#ff8a9b}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 20% 0%,#172547 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1120px;margin:0 auto;padding:38px 24px 64px}.login{max-width:420px;margin:10vh auto;background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:32px}.brand{display:flex;align-items:center;gap:12px;margin-bottom:30px}.mark{width:42px;height:42px;display:grid;place-items:center;border-radius:14px;background:#29385d;color:#dce4ff;font-weight:800;font-size:20px}.brand b,.brand small{display:block}.brand small,.muted,.hint{color:var(--muted)}h1{font-size:32px;line-height:1.2;margin:12px 0}h2{font-size:19px;margin:0 0 4px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:26px}.top h1{margin:0}.top p{margin:5px 0 0}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}.card{background:linear-gradient(150deg,#141d33,#101729);border:1px solid var(--line);border-radius:16px;padding:22px;min-width:0}.wide{grid-column:1/-1}.card-head{display:flex;justify-content:space-between;align-items:start;gap:16px;margin-bottom:18px}.card-head p{margin:2px 0 0;color:var(--muted);font-size:13px}label{display:block;font-weight:600;margin:14px 0 6px}input,textarea,select{display:block;width:100%;margin-top:6px;padding:11px 12px;border:1px solid #34425e;border-radius:9px;background:#0c1323;color:var(--text);font:inherit}textarea{min-height:96px;resize:vertical}button,.button{display:inline-flex;justify-content:center;align-items:center;border:0;border-radius:9px;background:var(--accent);color:#10172a;font:700 14px system-ui;padding:10px 15px;cursor:pointer;text-decoration:none;margin-top:15px}button:hover,.button:hover{filter:brightness(1.08)}.secondary{background:#26334e;color:var(--text)}.logout{margin:0}.logout button{margin:0}.inline{display:flex;align-items:end;gap:10px}.inline>*{flex:1}.inline button{flex:0 0 auto}.success{color:var(--green);padding:10px 12px;background:#102b29;border:1px solid #235148;border-radius:9px}.error{color:var(--red);padding:10px 12px;background:#321b28;border:1px solid #593040;border-radius:9px}.hint{font-size:12px}.suggestion{padding:16px 0;border-top:1px solid var(--line)}.suggestion:first-of-type{border-top:0}.suggestion-title{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:13px}.suggestion p{white-space:pre-wrap;overflow-wrap:anywhere;margin:9px 0}.suggestion form{display:grid;grid-template-columns:minmax(120px,180px) minmax(180px,1fr) auto;gap:10px;align-items:end}.suggestion form label{margin:0}.suggestion form button{margin:0}.empty{color:var(--muted);padding:14px 0}@media(max-width:760px){main{padding:24px 14px}.grid{grid-template-columns:1fr}.wide{grid-column:auto}.top{align-items:flex-start}.suggestion form{grid-template-columns:1fr}.suggestion form button{width:100%}}
"""


def _dashboard_html(request: web.Request, guild_id: int | None, message: str = "") -> str:
    csrf = request["csrf"]
    guild_ids = database.get_dashboard_guild_ids()
    selected = guild_id if guild_id is not None else (guild_ids[0] if guild_ids else None)
    notice = f'<p class="success">{_escape(message)}</p>' if message else ""

    options = ['<option value="">Choose a saved server…</option>']
    for item in guild_ids:
        chosen = " selected" if item == selected else ""
        options.append(f'<option value="{item}"{chosen}>{item}</option>')
    selector = "".join(options)
    server_picker = f"""<form class="inline" method="get" action="/">
<label>Saved server<select name="guild_id">{selector}</select></label><button class="secondary" type="submit">Open</button></form>
<form class="inline" method="get" action="/"><label>Open by server ID<input name="guild_id" type="number" min="1" step="1" placeholder="Discord server ID" required></label><button class="secondary" type="submit">Open</button></form>"""

    if selected is None:
        content = '<section class="card wide"><h2>Choose a server</h2><p class="empty">No server settings or suggestions are stored yet. Enter a Discord server ID above to create its welcome settings.</p></section>'
    else:
        settings = database.get_guild_settings(selected)
        channel_id = settings["welcome_channel_id"] or ""
        content = f"""<section class="card">
<div class="card-head"><div><h2>Welcome message</h2><p>Server ID {selected}</p></div></div>
<form method="post" action="/settings"><input type="hidden" name="csrf" value="{_escape(csrf)}"><input type="hidden" name="guild_id" value="{selected}">
<label>Destination channel ID<input name="welcome_channel_id" type="number" min="1" value="{_escape(channel_id)}" placeholder="Blank uses the server system channel"></label>
<label>Embed title<input name="welcome_title" maxlength="256" value="{_escape(settings['welcome_title'])}" required></label>
<label>Message<textarea name="welcome_message" maxlength="4096" required>{_escape(settings['welcome_message'])}</textarea></label>
<p class="hint">Supported placeholders: {{mention}}, {{server}}, {{member_count}}, {{username}}</p>
<label>Embed color<input name="welcome_color" value="{_escape(settings['welcome_color'])}" pattern="#?[0-9a-fA-F]{{6}}" required></label>
<button type="submit">Save welcome settings</button></form></section>"""

        suggestions = database.get_dashboard_suggestions(selected)
        if suggestions:
            cards = []
            for item in suggestions:
                status_options = []
                for status in sorted(ALLOWED_STATUSES):
                    chosen = " selected" if status == item["status"] else ""
                    status_options.append(
                        f'<option value="{status}"{chosen}>{status.title()}</option>'
                    )
                note = item.get("status_note") or ""
                cards.append(
                    f"""<article class="suggestion"><div class="suggestion-title"><span>#{item['id']} · {_escape(item['submitter'])} (user {item['user_id']})</span><span>{_escape(item['created_at'])}</span></div>
<p>{_escape(item['suggestion'])}</p>
<form method="post" action="/suggestions/status"><input type="hidden" name="csrf" value="{_escape(csrf)}"><input type="hidden" name="guild_id" value="{selected}"><input type="hidden" name="suggestion_id" value="{item['id']}">
<label>Status<select name="status">{''.join(status_options)}</select></label><label>Reviewer note<input name="note" maxlength="500" value="{_escape(note)}" placeholder="Optional note"></label><button type="submit">Update</button></form></article>"""
                )
            suggestion_content = "".join(cards)
        else:
            suggestion_content = '<p class="empty">No suggestions for this server yet.</p>'
        content += f"""<section class="card wide"><div class="card-head"><div><h2>Feature suggestions</h2><p>Recent 100 submissions for server {selected}. Status changes here do not send Discord DMs.</p></div></div>{suggestion_content}</section>"""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Nov Bot · Management</title><style>{_CSS}</style></head>
<body><main><header class="top"><div><div class="brand"><span class="mark">N</span><div><b>Nov Bot</b><small>Management panel</small></div></div><h1>Dashboard</h1><p class="muted">Welcome settings and community suggestions</p></div>
<form class="logout" method="post" action="/logout"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button class="secondary" type="submit">Sign out</button></form></header>
{notice}<section class="card" style="margin-bottom:18px"><div class="card-head"><div><h2>Server</h2><p>Choose a server or enter its Discord ID</p></div></div>{server_picker}</section>
<div class="grid">{content}</div></main></body></html>"""


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok", content_type="text/plain")


async def login_page(request: web.Request) -> web.Response:
    if _read_session(request.cookies.get(COOKIE_NAME)):
        raise web.HTTPSeeOther("/")
    return web.Response(text=_login_html(), content_type="text/html")


async def login(request: web.Request) -> web.Response:
    now = time.monotonic()
    ip = request.remote or "unknown"
    attempts = LOGIN_ATTEMPTS[ip]
    while attempts and now - attempts[0] > 60:
        attempts.popleft()
    if len(attempts) >= 5:
        raise web.HTTPTooManyRequests(text="Too many sign-in attempts. Wait one minute and retry.")

    form = await request.post()
    supplied = str(form.get("password", ""))
    if not hmac.compare_digest(
        supplied.encode("utf-8"), config.WEBUI_PASSWORD.encode("utf-8")
    ):
        attempts.append(now)
        return web.Response(
            status=401,
            text=_login_html("That password was not accepted."),
            content_type="text/html",
        )

    attempts.clear()
    nonce = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + SESSION_TTL
    response = web.HTTPSeeOther("/")
    response.set_cookie(
        COOKIE_NAME,
        _session_cookie(nonce, expires_at),
        max_age=SESSION_TTL,
        httponly=True,
        secure=config.WEBUI_SECURE_COOKIE,
        samesite="Strict",
        path="/",
    )
    raise response


async def dashboard(request: web.Request) -> web.Response:
    raw_guild_id = request.query.get("guild_id", "")
    try:
        guild_id = int(raw_guild_id) if raw_guild_id else None
        if guild_id is not None and not 0 < guild_id <= 2**63 - 1:
            raise ValueError
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid server ID.")
    message = ""
    if request.query.get("saved") == "settings":
        message = "Welcome settings saved."
    elif request.query.get("saved") == "suggestion":
        message = "Suggestion status updated."
    return web.Response(
        text=_dashboard_html(request, guild_id, message), content_type="text/html"
    )


async def save_settings(request: web.Request) -> web.Response:
    form = await request.post()
    _check_csrf(request, form)
    try:
        guild_id = int(form.get("guild_id", ""))
        if not 0 < guild_id <= 2**63 - 1:
            raise ValueError
        raw_channel = str(form.get("welcome_channel_id", "")).strip()
        channel_id = int(raw_channel) if raw_channel else None
        if channel_id is not None and not 0 < channel_id <= 2**63 - 1:
            raise ValueError
    except ValueError:
        raise web.HTTPBadRequest(text="Server and channel IDs must be positive numbers.")

    title = str(form.get("welcome_title", "")).strip()
    message = str(form.get("welcome_message", "")).strip()
    color = str(form.get("welcome_color", "")).strip()
    if len(title) > 256 or not title or not _valid_template(title):
        raise web.HTTPBadRequest(text="Title must be 1–256 characters and use supported placeholders.")
    if len(message) > 4096 or not message or not _valid_template(message):
        raise web.HTTPBadRequest(text="Message must be 1–4096 characters and use supported placeholders.")
    if color.startswith("#"):
        color = color[1:]
    if len(color) != 6 or any(character not in "0123456789abcdefABCDEF" for character in color):
        raise web.HTTPBadRequest(text="Color must be a six-digit hexadecimal value.")

    database.update_guild_setting(guild_id, "welcome_channel_id", channel_id)
    database.update_guild_setting(guild_id, "welcome_title", title)
    database.update_guild_setting(guild_id, "welcome_message", message)
    database.update_guild_setting(guild_id, "welcome_color", f"#{color.upper()}")
    raise web.HTTPSeeOther(f"/?{urlencode({'guild_id': guild_id, 'saved': 'settings'})}")


async def update_suggestion(request: web.Request) -> web.Response:
    form = await request.post()
    _check_csrf(request, form)
    try:
        guild_id = int(form.get("guild_id", ""))
        suggestion_id = int(form.get("suggestion_id", ""))
        if not (0 < guild_id <= 2**63 - 1 and 0 < suggestion_id <= 2**63 - 1):
            raise ValueError
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid server or suggestion ID.")

    status = str(form.get("status", ""))
    note = str(form.get("note", "")).strip() or None
    if status not in ALLOWED_STATUSES or (note and len(note) > 500):
        raise web.HTTPBadRequest(text="Choose a valid status and keep notes under 500 characters.")
    updated = database.set_suggestion_status(
        guild_id, suggestion_id, status, note, reviewer_id=0
    )
    if updated is None:
        raise web.HTTPNotFound(text="Suggestion not found for that server.")
    raise web.HTTPSeeOther(f"/?{urlencode({'guild_id': guild_id, 'saved': 'suggestion'})}")


async def logout(request: web.Request) -> web.Response:
    form = await request.post()
    _check_csrf(request, form)
    response = web.HTTPSeeOther("/login")
    response.del_cookie(COOKIE_NAME, path="/")
    raise response


def create_app() -> web.Application:
    if len(config.WEBUI_PASSWORD) < 16:
        raise RuntimeError(
            "Set WEB_PANEL_PASSWORD to a password with at least 16 characters before starting the panel."
        )
    database.initialize()
    app = web.Application(middlewares=[security_middleware], client_max_size=32 * 1024)
    app.add_routes(
        [
            web.get("/healthz", health),
            web.get("/login", login_page),
            web.post("/login", login),
            web.get("/", dashboard),
            web.post("/settings", save_settings),
            web.post("/suggestions/status", update_suggestion),
            web.post("/logout", logout),
        ]
    )
    return app


if __name__ == "__main__":
    if not config.WEBUI_SECURE_COOKIE:
        print(
            "WARNING: WEBUI_SECURE_COOKIE is off. Use an HTTPS proxy before exposing the panel publicly."
        )
    web.run_app(
        create_app(),
        host=config.WEBUI_HOST,
        port=config.WEBUI_PORT,
        print=lambda message: print(f"[webpanel] {message}"),
    )
