"""Password-protected local management panel for welcome and suggestion data."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from string import Formatter
from urllib.parse import urlsplit, urlencode

from aiohttp import web

import config
import database
import server_builder

COOKIE_NAME = "nov_web_session"
LAST_GUILD_COOKIE = "nov_last_guild"
AUTH_LOG = logging.getLogger("webpanel.auth")
SESSION_TTL = 8 * 60 * 60
# Keep signed sessions valid when Pterodactyl restarts only the panel process.
# Store a random key beside the app instead of regenerating it at each startup.
_SESSION_KEY_FILE = Path(__file__).with_name(".webpanel-session-key")
try:
    SESSION_SECRET = _SESSION_KEY_FILE.read_bytes()
except FileNotFoundError:
    SESSION_SECRET = secrets.token_bytes(32)
    try:
        fd = os.open(_SESSION_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        SESSION_SECRET = _SESSION_KEY_FILE.read_bytes()
    else:
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(SESSION_SECRET)
LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
BUILDER_SESSIONS: dict[str, dict] = {}
BUILDER_SESSION_TTL = 2 * 60 * 60
BUILDER_MAX_SESSIONS = 256
BUILDER_GLOBAL_REQUESTS: deque[float] = deque()
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
            if request.path != "/login":
                AUTH_LOG.warning("Protected page requested without a valid panel session")
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
    # A Pterodactyl egg/reverse proxy may not expose its public origin to the
    # app. Enforce the explicit origin when configured; otherwise the
    # session-bound CSRF token below remains the required protection.
    if origin and config.WEBUI_PUBLIC_ORIGIN:
        expected_origin = config.WEBUI_PUBLIC_ORIGIN
        try:
            supplied = urlsplit(origin)
            expected = urlsplit(expected_origin)
            valid_origin = (
                supplied.scheme.lower() in {"http", "https"}
                and supplied.netloc
                and not supplied.username
                and not supplied.password
                and supplied.scheme.lower() == expected.scheme.lower()
                and supplied.netloc.lower() == expected.netloc.lower()
                and not supplied.path
                and not supplied.query
                and not supplied.fragment
            )
        except ValueError:
            valid_origin = False
        if not valid_origin:
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
    guilds = database.get_dashboard_guilds()
    guild_ids = [int(item["guild_id"]) for item in guilds]
    selected = guild_id if guild_id is not None else (guild_ids[0] if guild_ids else None)
    notice = f'<p class="success">{_escape(message)}</p>' if message else ""

    options = ['<option value="">Choose a saved server…</option>']
    for item in guilds:
        guild_id = int(item["guild_id"])
        guild_name = str(item.get("guild_name") or f"Server {guild_id}")
        chosen = " selected" if guild_id == selected else ""
        options.append(
            f'<option value="{guild_id}"{chosen}>{_escape(guild_name)} · {guild_id}</option>'
        )
    selector = "".join(options)
    builder_href = f"/builder?{urlencode({'guild_id': selected})}" if selected else "/builder"
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

        ticket_config = database.get_ticket_config(selected)
        category_lines = [
            " | ".join((category.get("emoji") or "", category["name"], category["description"], str(category.get("parent_category_id") or "")))
            for category in ticket_config["categories"]
        ]
        category_text = "\n".join(category_lines)
        admin_roles = ", ".join(str(role_id) for role_id in ticket_config["admin_role_ids"])
        content += f"""<section class="card wide"><div class="card-head"><div><h2>Support tickets</h2><p>Configure the Discord ticket panel, staff roles, and ticket categories for server {selected}.</p></div></div>
<form method="post" action="/tickets/settings"><input type="hidden" name="csrf" value="{_escape(csrf)}"><input type="hidden" name="guild_id" value="{selected}">
<div class="inline"><label>Panel channel ID<input name="panel_channel_id" type="number" min="1" value="{_escape(ticket_config['panel_channel_id'] or '')}" placeholder="Discord text channel ID"></label><label>Log channel ID<input name="log_channel_id" type="number" min="1" value="{_escape(ticket_config['log_channel_id'] or '')}" placeholder="Discord text channel ID"></label></div>
<label>Admin role IDs<input name="admin_role_ids" value="{_escape(admin_roles)}" placeholder="Comma-separated role IDs"></label>
<label>Categories<textarea name="categories" maxlength="10000" placeholder="🎫 | General Support | Ask us anything |\n🛠️ | Technical Help | Get help with a technical issue | 123456789012345678">{_escape(category_text)}</textarea></label>
<p class="hint">One category per line: emoji | title | description | optional Discord category channel ID. Leave the emoji or final channel ID blank if unused. Up to 25 categories.</p>
<button type="submit">Save ticket settings</button></form></section>"""

        role_picker = database.get_role_picker_config(selected)
        role_ids = ", ".join(str(role_id) for role_id in role_picker["role_ids"])
        single_selected = " selected" if not role_picker["allow_multiple"] else ""
        multiple_selected = " selected" if role_picker["allow_multiple"] else ""
        content += f"""<section class="card wide"><div class="card-head"><div><h2>Self-assign role panel</h2><p>Members choose only from the roles listed here. The bot adds or removes the chosen roles.</p></div></div>
<form method="post" action="/role-picker/settings"><input type="hidden" name="csrf" value="{_escape(csrf)}"><input type="hidden" name="guild_id" value="{selected}">
<div class="inline"><label>Panel channel ID<input name="panel_channel_id" type="number" min="1" value="{_escape(role_picker['panel_channel_id'] or '')}" placeholder="Discord text channel ID"></label><label>Selection mode<select name="mode"><option value="single"{single_selected}>One role at a time</option><option value="multiple"{multiple_selected}>Multiple roles</option></select></label></div>
<label>Panel title<input name="title" maxlength="256" value="{_escape(role_picker['title'])}" required></label>
<label>Panel description<textarea name="description" maxlength="4000" required>{_escape(role_picker['description'])}</textarea></label>
<label>Available role IDs<input name="role_ids" value="{_escape(role_ids)}" placeholder="Comma-separated Discord role IDs"></label>
<p class="hint">Up to 25 regular roles. Admin, moderation, and management-permission roles are blocked. The bot needs Manage Roles, and each role must be below its highest role. Saving updates the panel automatically.</p>
<button type="submit">Save role panel</button></form></section>"""

        verification = database.get_verification_config(selected)
        content += f"""<section class="card wide"><div class="card-head"><div><h2>Verification panel</h2><p>Members solve a short image CAPTCHA before receiving the verified role.</p></div></div>
<form method="post" action="/verification/settings"><input type="hidden" name="csrf" value="{_escape(csrf)}"><input type="hidden" name="guild_id" value="{selected}">
<div class="inline"><label>Panel channel ID<input name="panel_channel_id" type="number" min="1" value="{_escape(verification['panel_channel_id'] or '')}" placeholder="Discord text channel ID" required></label><label>Verified role ID<input name="verified_role_id" type="number" min="1" value="{_escape(verification['verified_role_id'] or '')}" placeholder="Discord role ID"></label></div>
<label>Panel title<input name="title" maxlength="256" value="{_escape(verification['title'])}" required></label>
<label>Panel description<textarea name="description" maxlength="4000" required>{_escape(verification['description'])}</textarea></label>
<p class="hint">Use a regular role with no admin, moderation, or management permissions. The bot needs Manage Roles, and the verified role must be below its highest role. Challenges expire after five minutes.</p>
<button type="submit">Save verification panel</button></form></section>"""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Nov Bot · Management</title><style>{_CSS}</style></head>
<body><main><header class="top"><div><div class="brand"><span class="mark">N</span><div><b>Nov Bot</b><small>Management panel</small></div></div><h1>Dashboard</h1><p class="muted">Server settings, roles, tickets, and community tools</p></div>
<div class="inline"><a class="button secondary" href="{_escape(builder_href)}">AI server builder</a><form class="logout" method="post" action="/logout"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button class="secondary" type="submit">Sign out</button></form></div></header>
{notice}<section class="card" style="margin-bottom:18px"><div class="card-head"><div><h2>Server</h2><p>Choose a server or enter its Discord ID</p></div></div>{server_picker}</section>
<div class="grid">{content}</div></main></body></html>"""


_BUILDER_CSS = """
.builder-layout{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(320px,.95fr);gap:18px;align-items:start}.builder-card{min-height:480px}.chat-log{height:340px;overflow:auto;display:flex;flex-direction:column;gap:12px;padding:12px;background:#0c1323;border:1px solid var(--line);border-radius:12px}.bubble{max-width:92%;padding:12px 14px;border-radius:13px;white-space:pre-wrap;overflow-wrap:anywhere}.bubble.user{align-self:flex-end;background:#26375b}.bubble.assistant{align-self:flex-start;background:#17243b}.bubble small{display:block;color:var(--muted);font-size:11px;margin-bottom:4px}.plan-list{margin:12px 0 0;padding-left:20px}.plan-list li{margin:5px 0}.plan-section{border-top:1px solid var(--line);padding:13px 0}.plan-section:first-child{border-top:0}.role-preview{border-left:3px solid #34425e;padding:6px 10px;margin:8px 0;background:#10192b;border-radius:3px 8px 8px 3px}.role-preview small{display:block;color:var(--muted);margin-top:3px}.builder-select{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:end}.builder-select label{margin:0}.confirm{font-weight:500!important;display:flex;gap:9px;align-items:flex-start}.confirm input{width:auto;margin:3px 0 0}.empty-plan{color:var(--muted);padding:24px 0}.builder-header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}.builder-header a{color:var(--accent)}
@media(max-width:820px){.builder-layout{grid-template-columns:1fr}.builder-card{min-height:0}.chat-log{height:300px}.builder-select{grid-template-columns:1fr}}
"""


def _builder_state(request: web.Request) -> dict:
    now = time.time()
    for nonce, state in list(BUILDER_SESSIONS.items()):
        if now - state["touched"] > BUILDER_SESSION_TTL:
            BUILDER_SESSIONS.pop(nonce, None)
    nonce = request["session_nonce"]
    state = BUILDER_SESSIONS.get(nonce)
    if state is None:
        if len(BUILDER_SESSIONS) >= BUILDER_MAX_SESSIONS:
            oldest = min(BUILDER_SESSIONS, key=lambda key: BUILDER_SESSIONS[key]["touched"])
            BUILDER_SESSIONS.pop(oldest, None)
        state = {
            "touched": now,
            "guild_id": None,
            "history": [],
            "messages": [],
            "plan": None,
            "plan_id": None,
            "applied": False,
            "notice": None,
            "last_chat": 0.0,
            "lock": asyncio.Lock(),
        }
        BUILDER_SESSIONS[nonce] = state
    state["touched"] = now
    return state


def _parse_guild_id(value: object) -> int | None:
    try:
        guild_id = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return guild_id if 0 < guild_id < 10**20 else None


def _builder_html(
    request: web.Request,
    state: dict,
    guild_id: int | None,
    guild_name: str,
) -> str:
    csrf = _escape(request["csrf"])
    notice = ""
    if state["notice"]:
        kind, message = state["notice"]
        notice = f'<p class="{kind}">{_escape(message)}</p>'
    guilds = database.get_dashboard_guilds()
    selected = "" if guild_id is None else str(guild_id)
    options = ['<option value="">Choose a saved server…</option>']
    for item in guilds:
        item_id = str(item["guild_id"])
        chosen = " selected" if item_id == selected else ""
        options.append(
            f'<option value="{_escape(item_id)}"{chosen}>{_escape(item.get("guild_name") or "Server")} · {_escape(item_id)}</option>'
        )

    messages = []
    for item in state["messages"]:
        role = "user" if item["role"] == "user" else "assistant"
        label = "You" if role == "user" else "Nov AI"
        messages.append(
            f'<div class="bubble {role}"><small>{label}</small>{_escape(item["text"])}</div>'
        )
    if not messages:
        messages.append(
            '<div class="bubble assistant"><small>Nov AI</small>Describe the community you want. I’ll draft categories, channels, and roles for review.</div>'
        )

    plan = state["plan"]
    if plan:
        category_rows = "".join(
            f'<li><strong>{_escape(item["name"])}</strong> <span class="hint">category</span></li>'
            for item in plan["categories"]
        )
        channel_items = []
        for item in plan["channels"]:
            category_label = f' · {_escape(item["category"])}' if item["category"] else ""
            topic_label = f'<br><span class="hint">{_escape(item["topic"])}</span>' if item["topic"] else ""
            channel_items.append(
                f'<li><strong>#{_escape(item["name"])}</strong> · {_escape(item["type"])}{category_label}{topic_label}</li>'
            )
        channel_rows = "".join(channel_items)
        role_items = []
        for item in plan["roles"]:
            color = item["color_hex"] if item["color_hex"] != "Default" else "#34425e"
            permissions = ", ".join(item["permissions"]) or "No extra permissions"
            flags = []
            if item["hoist"]:
                flags.append("shown separately")
            if item["mentionable"]:
                flags.append("mentionable")
            detail = f'{permissions} · {", ".join(flags)}' if flags else permissions
            role_items.append(
                f'<div class="role-preview" style="border-left-color:{_escape(color)}"><strong>{_escape(item["name"])}</strong><small>{_escape(detail)}</small></div>'
            )
        role_rows = "".join(role_items) or '<p class="hint">No roles proposed.</p>'
        plan_content = f"""<p class="success">{_escape(plan['summary'] or 'Plan ready for review.')}</p>
<div class="plan-section"><strong>Categories ({len(plan['categories'])})</strong><ul class="plan-list">{category_rows or '<li class="hint">None</li>'}</ul></div>
<div class="plan-section"><strong>Channels ({len(plan['channels'])})</strong><ul class="plan-list">{channel_rows or '<li class="hint">None</li>'}</ul></div>
<div class="plan-section"><strong>Roles ({len(plan['roles'])})</strong><div>{role_rows}</div><p class="hint">New roles are not assigned to members automatically.</p></div>
<p class="hint">Applying creates the listed resources on { _escape(guild_name) }. Existing items are not edited or deleted.</p>"""
        if state["applied"]:
            plan_content += '<p class="success">This plan has already been applied.</p>'
        elif plan["categories"] or plan["channels"] or plan["roles"]:
            plan_content += f"""<form method="post" action="/builder/apply">
<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="guild_id" value="{selected}"><input type="hidden" name="plan_id" value="{_escape(state['plan_id'])}">
<label class="confirm"><input type="checkbox" name="confirm" value="yes" required><span>I reviewed this plan and want to create these resources.</span></label>
<button type="submit">Apply plan to server</button></form>"""
        else:
            plan_content += '<p class="hint">This plan has no changes to apply.</p>'
    else:
        plan_content = '<p class="empty-plan">Your reviewed plan will appear here after you describe what you want.</p>'

    key_notice = ""
    if not config.GROQ_API_KEY:
        key_notice = '<p class="hint">To enable AI planning, add a Groq API key as <code>GROQ_API_KEY</code> in the server .env file, then restart the bot.</p>'
    server_form = f"""<form class="builder-select" method="get" action="/builder"><label>Saved server<select name="guild_id">{''.join(options)}</select></label><button class="secondary" type="submit">Open</button></form>
<form class="builder-select" method="get" action="/builder"><label>Or enter server ID<input name="guild_id" type="number" min="1" step="1" value="{selected}" placeholder="Discord server ID" required></label><button class="secondary" type="submit">Open</button></form>"""
    current_server = (
        f'<p class="hint">Selected server: <strong>{_escape(guild_name)}</strong> · {selected}</p>'
        if guild_id is not None
        else '<p class="hint">Choose a saved server or enter its ID. The bot must be a member of that server.</p>'
    )
    chat_form = ""
    if guild_id is not None:
        chat_form = f"""<form method="post" action="/builder/chat"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="guild_id" value="{selected}">
<label for="builder-prompt">What should this server look like?</label><textarea id="builder-prompt" name="message" maxlength="2000" required placeholder="A cozy gaming community with announcements, LFG channels, and moderator roles"></textarea>
<button type="submit">Draft preview</button></form>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Nov Bot · AI server builder</title><style>{_CSS}{_BUILDER_CSS}</style></head>
<body><main><header class="builder-header"><div><a href="/">← Management dashboard</a><h1>AI server builder</h1><p class="muted">Chat to draft a plan, review it, then choose when to apply it.</p></div><form class="logout" method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button class="secondary" type="submit">Sign out</button></form></header>
{notice}<section class="card" style="margin-bottom:18px"><div class="card-head"><div><h2>Choose server</h2><p>The plan is scoped to one server at a time.</p></div></div>{server_form}{current_server}{key_notice}</section>
<div class="builder-layout"><section class="card builder-card"><div class="card-head"><div><h2>Design chat</h2><p>Nothing changes on Discord until you apply a reviewed plan.</p></div></div><div class="chat-log">{''.join(messages)}</div>{chat_form}</section>
<section class="card builder-card"><div class="card-head"><div><h2>Plan preview</h2><p>{_escape(guild_name) if guild_id else 'Choose a server to begin'}</p></div></div>{plan_content}</section></div>
<p class="hint" style="margin-top:18px">The builder only creates categories, text/voice channels, and roles. It will not remove or change existing resources.</p></main></body></html>"""


def _guild_label(guild_id: int) -> str:
    for item in database.get_dashboard_guilds():
        if int(item["guild_id"]) == guild_id:
            return str(item.get("guild_name") or f"Server {guild_id}")
    return f"Server {guild_id}"


async def builder_page(request: web.Request) -> web.Response:
    state = _builder_state(request)
    requested = request.query.get("guild_id")
    if requested is not None:
        guild_id = _parse_guild_id(requested)
        if requested and guild_id is None:
            state["notice"] = ("error", "Enter a valid Discord server ID.")
            guild_id = None
    else:
        guild_id = (
            _parse_guild_id(state["guild_id"])
            or _parse_guild_id(request.cookies.get(LAST_GUILD_COOKIE))
        )
        if guild_id is None:
            saved = database.get_dashboard_guilds()
            if saved:
                guild_id = _parse_guild_id(saved[0]["guild_id"])

    if guild_id != state["guild_id"]:
        state.update(
            guild_id=guild_id,
            history=[],
            messages=[],
            plan=None,
            plan_id=None,
            applied=False,
            notice=state["notice"] if requested is not None and guild_id is None else None,
        )
    response = web.Response(
        text=_builder_html(request, state, guild_id, _guild_label(guild_id) if guild_id else ""),
        content_type="text/html",
    )
    if guild_id:
        response.set_cookie(
            LAST_GUILD_COOKIE,
            str(guild_id),
            max_age=SESSION_TTL,
            httponly=True,
            secure=config.WEBUI_SECURE_COOKIE,
            samesite="Lax",
            path="/",
        )
    return response


def _builder_redirect(guild_id: int) -> web.HTTPSeeOther:
    return web.HTTPSeeOther(f"/builder?{urlencode({'guild_id': guild_id})}")


async def builder_chat(request: web.Request) -> web.Response:
    form = await request.post()
    _check_csrf(request, form)
    guild_id = _parse_guild_id(form.get("guild_id"))
    message = str(form.get("message", "")).strip()
    state = _builder_state(request)
    if guild_id is None or guild_id != state["guild_id"]:
        raise web.HTTPBadRequest(text="Choose a server before sending a design request.")
    if not message or len(message) > 2000:
        state["notice"] = ("error", "Describe your server in 1–2000 characters.")
        raise _builder_redirect(guild_id)

    async with state["lock"]:
        now = time.monotonic()
        if now - state["last_chat"] < 3:
            state["notice"] = ("error", "Please wait a few seconds before sending another request.")
            raise _builder_redirect(guild_id)
        while BUILDER_GLOBAL_REQUESTS and now - BUILDER_GLOBAL_REQUESTS[0] > 60:
            BUILDER_GLOBAL_REQUESTS.popleft()
        if len(BUILDER_GLOBAL_REQUESTS) >= 20:
            state["notice"] = ("error", "The AI planner is busy. Wait a minute before trying again.")
            raise _builder_redirect(guild_id)
        state["last_chat"] = now
        BUILDER_GLOBAL_REQUESTS.append(now)
        try:
            context = await server_builder.get_guild_context(guild_id)
            if not context["can_manage_channels"] and not context["can_manage_roles"]:
                raise server_builder.BuilderError(
                    "The bot needs Manage Channels or Manage Roles permission to build this server."
                )
            history = state["history"] + [{"role": "user", "parts": [{"text": message}]}]
            plan = await server_builder.generate_plan(history[-16:], context)
            model_context = (
                f"{plan['reply']}\nCurrent proposed plan JSON: "
                + json.dumps(
                    {key: plan[key] for key in ("summary", "categories", "channels", "roles")},
                    ensure_ascii=False,
                )
            )
            state["history"] = (
                history + [{"role": "model", "parts": [{"text": model_context}]}]
            )[-16:]
            state["messages"].extend(
                [{"role": "user", "text": message}, {"role": "model", "text": plan["reply"] or "Plan drafted. Review the preview."}]
            )
            state["messages"] = state["messages"][-24:]
            state["plan"] = plan
            state["plan_id"] = secrets.token_urlsafe(24)
            state["applied"] = False
            state["notice"] = ("success", "Draft ready. Review every item in the preview before applying it.")
        except server_builder.BuilderError as error:
            state["messages"].append({"role": "user", "text": message})
            state["messages"] = state["messages"][-24:]
            state["notice"] = ("error", str(error))
        except Exception:
            logging.exception("AI server builder failed while preparing a plan")
            state["notice"] = ("error", "The planner could not prepare a preview. Try again in a moment.")
    raise _builder_redirect(guild_id)


async def builder_apply(request: web.Request) -> web.Response:
    form = await request.post()
    _check_csrf(request, form)
    guild_id = _parse_guild_id(form.get("guild_id"))
    state = _builder_state(request)
    supplied_plan = str(form.get("plan_id", ""))
    if guild_id is None or guild_id != state["guild_id"]:
        raise web.HTTPBadRequest(text="The selected server changed. Reload the builder and review the plan again.")

    async with state["lock"]:
        if (
            form.get("confirm") != "yes"
            or state["plan"] is None
            or state["applied"]
            or not state["plan_id"]
            or not hmac.compare_digest(supplied_plan, state["plan_id"])
        ):
            state["notice"] = ("error", "Review the current plan and check the confirmation box before applying it.")
            raise _builder_redirect(guild_id)
        try:
            created = await server_builder.apply_plan(guild_id, state["plan"])
            state["applied"] = True
            state["notice"] = ("success", f"Created {len(created)} server resources successfully.")
        except server_builder.BuilderError as error:
            partial = getattr(error, "created", [])
            if partial:
                state["applied"] = True
                summary = ", ".join(partial[:8])
                suffix = " and more" if len(partial) > 8 else ""
                state["notice"] = (
                    "error",
                    f"Discord stopped the operation after creating {len(partial)} items: {summary}{suffix}. This plan is locked to prevent duplicate creation. {error}",
                )
            else:
                state["notice"] = ("error", str(error))
        except Exception:
            logging.exception("AI server builder failed while applying a plan")
            state["notice"] = ("error", "The plan could not be applied. Review the server and retry if no items were created.")
    raise _builder_redirect(guild_id)


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
        AUTH_LOG.warning("Panel login rejected; submitted password did not match configuration")
        attempts.append(now)
        return web.Response(
            status=401,
            text=_login_html("That password was not accepted."),
            content_type="text/html",
        )

    attempts.clear()
    AUTH_LOG.info("Panel login accepted; issuing session cookie")
    nonce = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + SESSION_TTL
    response = web.Response(status=303, headers={"Location": "/"})
    response.set_cookie(
        COOKIE_NAME,
        _session_cookie(nonce, expires_at),
        max_age=SESSION_TTL,
        httponly=True,
        secure=config.WEBUI_SECURE_COOKIE,
        samesite="Lax",
        path="/",
    )
    return response


async def dashboard(request: web.Request) -> web.Response:
    raw_guild_id = request.query.get("guild_id", "")
    if not raw_guild_id:
        remembered = request.cookies.get(LAST_GUILD_COOKIE, "")
        if remembered.isdigit() and 0 < int(remembered) <= 2**63 - 1:
            raise web.HTTPSeeOther(f"/?{urlencode({'guild_id': int(remembered)})}")
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
    elif request.query.get("saved") == "tickets":
        message = "Ticket settings saved. The Discord panel will update shortly."
    elif request.query.get("saved") == "role_picker":
        message = "Role picker settings saved. The Discord panel will update shortly."
    elif request.query.get("saved") == "verification":
        message = "Verification settings saved. The Discord panel will update shortly."
    response = web.Response(
        text=_dashboard_html(request, guild_id, message), content_type="text/html"
    )
    if guild_id is not None:
        response.set_cookie(
            LAST_GUILD_COOKIE,
            str(guild_id),
            max_age=365 * 24 * 60 * 60,
            httponly=True,
            secure=config.WEBUI_SECURE_COOKIE,
            samesite="Lax",
            path="/",
        )
    return response


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


async def save_ticket_settings(request: web.Request) -> web.Response:
    form = await request.post()
    _check_csrf(request, form)
    try:
        guild_id = int(form.get("guild_id", ""))
        if not 0 < guild_id <= 2**63 - 1:
            raise ValueError
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid server ID.")

    def parse_optional_id(value: object, label: str) -> int | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = int(raw)
        except ValueError as error:
            raise web.HTTPBadRequest(text=f"{label} must be a positive Discord ID.") from error
        if not 0 < parsed <= 2**63 - 1:
            raise web.HTTPBadRequest(text=f"{label} must be a positive Discord ID.")
        return parsed

    panel_channel_id = parse_optional_id(form.get("panel_channel_id"), "Panel channel")
    log_channel_id = parse_optional_id(form.get("log_channel_id"), "Log channel")
    role_text = str(form.get("admin_role_ids", ""))
    admin_role_ids = []
    for raw_role_id in (part.strip() for part in role_text.split(",")):
        if not raw_role_id:
            continue
        role_id = parse_optional_id(raw_role_id, "Admin role")
        if role_id not in admin_role_ids:
            admin_role_ids.append(role_id)
    if len(admin_role_ids) > 25:
        raise web.HTTPBadRequest(text="Configure no more than 25 admin roles.")

    categories = []
    for line_number, line in enumerate(str(form.get("categories", "")).splitlines(), 1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|", 3)]
        if len(parts) < 3:
            raise web.HTTPBadRequest(
                text=f"Category line {line_number} needs emoji | title | description."
            )
        emoji, name, description = parts[:3]
        parent_id = parse_optional_id(parts[3], f"Category line {line_number} parent channel") if len(parts) == 4 else None
        if not name or len(name) > 100 or not description or len(description) > 100:
            raise web.HTTPBadRequest(
                text=f"Category line {line_number}: title and description must be 1–100 characters."
            )
        if len(emoji) > 64:
            raise web.HTTPBadRequest(text=f"Category line {line_number}: emoji is too long.")
        categories.append(
            {
                "emoji": emoji,
                "name": name,
                "description": description,
                "parent_category_id": parent_id,
            }
        )
        if len(categories) > 25:
            raise web.HTTPBadRequest(text="Configure no more than 25 ticket categories.")

    if categories and panel_channel_id is None:
        raise web.HTTPBadRequest(text="Set a panel channel before enabling ticket categories.")
    database.save_ticket_config(
        guild_id, panel_channel_id, log_channel_id, admin_role_ids, categories
    )
    raise web.HTTPSeeOther(f"/?{urlencode({'guild_id': guild_id, 'saved': 'tickets'})}")


def _form_guild_id(form) -> int:
    try:
        guild_id = int(str(form.get("guild_id", "")))
    except ValueError as error:
        raise web.HTTPBadRequest(text="Invalid server ID.") from error
    if not 0 < guild_id <= 2**63 - 1:
        raise web.HTTPBadRequest(text="Invalid server ID.")
    return guild_id


def _form_optional_id(value: object, label: str) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        result = int(raw)
    except ValueError as error:
        raise web.HTTPBadRequest(text=f"{label} must be a positive Discord ID.") from error
    if not 0 < result <= 2**63 - 1:
        raise web.HTTPBadRequest(text=f"{label} must be a positive Discord ID.")
    return result


async def save_role_picker_settings(request: web.Request) -> web.Response:
    form = await request.post()
    _check_csrf(request, form)
    guild_id = _form_guild_id(form)
    panel_channel_id = _form_optional_id(form.get("panel_channel_id"), "Panel channel")
    if panel_channel_id is None:
        raise web.HTTPBadRequest(text="Set a panel channel ID.")
    title = str(form.get("title", "")).strip()
    description = str(form.get("description", "")).strip()
    if not title or len(title) > 256 or not description or len(description) > 4000:
        raise web.HTTPBadRequest(text="Panel title must be 1–256 characters and description 1–4000 characters.")
    mode = str(form.get("mode", "single"))
    if mode not in {"single", "multiple"}:
        raise web.HTTPBadRequest(text="Choose a valid role selection mode.")
    role_ids = []
    for raw in str(form.get("role_ids", "")).split(","):
        raw = raw.strip()
        if not raw:
            continue
        role_id = _form_optional_id(raw, "Role ID")
        if role_id not in role_ids:
            role_ids.append(role_id)
    if len(role_ids) > 25:
        raise web.HTTPBadRequest(text="Configure no more than 25 selectable roles.")
    database.save_role_picker_config(
        guild_id,
        panel_channel_id,
        title,
        description,
        role_ids,
        mode == "multiple",
    )
    raise web.HTTPSeeOther(f"/?{urlencode({'guild_id': guild_id, 'saved': 'role_picker'})}")


async def save_verification_settings(request: web.Request) -> web.Response:
    form = await request.post()
    _check_csrf(request, form)
    guild_id = _form_guild_id(form)
    panel_channel_id = _form_optional_id(form.get("panel_channel_id"), "Panel channel")
    verified_role_id = _form_optional_id(form.get("verified_role_id"), "Verified role")
    if panel_channel_id is None:
        raise web.HTTPBadRequest(text="Set a panel channel ID.")
    title = str(form.get("title", "")).strip()
    description = str(form.get("description", "")).strip()
    if not title or len(title) > 256 or not description or len(description) > 4000:
        raise web.HTTPBadRequest(text="Panel title must be 1–256 characters and description 1–4000 characters.")
    database.save_verification_config(
        guild_id, panel_channel_id, verified_role_id, title, description
    )
    raise web.HTTPSeeOther(f"/?{urlencode({'guild_id': guild_id, 'saved': 'verification'})}")


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
            web.get("/builder", builder_page),
            web.post("/builder/chat", builder_chat),
            web.post("/builder/apply", builder_apply),
            web.post("/settings", save_settings),
            web.post("/suggestions/status", update_suggestion),
            web.post("/tickets/settings", save_ticket_settings),
            web.post("/role-picker/settings", save_role_picker_settings),
            web.post("/verification/settings", save_verification_settings),
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
