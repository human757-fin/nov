# NOV BOT

Nov Bot is a multi-purpose Discord bot for your server. It uses Python 3.12+, `discord.py`, and SQLite or MySQL/MariaDB.

## Setup

1. Install dependencies with [uv](https://docs.astral.sh/uv/): `uv sync`.
2. Create a `.env` file in the project folder with the bot token and admin role:

   ```dotenv
   DISCORD_TOKEN=your-bot-token
   ADMIN_ROLE_ID=123456789012345678
   ```

   `ADMIN_ROLE_ID` can be `0` if you do not use a dedicated bot-admin role. Admin-level commands are limited to the server owner, Discord administrators, and members with this configured role.
3. In the Discord Developer Portal, enable the **Server Members Intent** and **Message Content Intent** for the bot. Invite it with the permissions needed for moderation and to send embeds.
4. Start it with `uv run python bot.py`.

## Management web panel

`webpanel.py` is a separate, password-protected management page for welcome settings and recent feature suggestions. It shares the bot database. Set Pterodactyl's `WEB_PANEL_PASSWORD` to a password with at least 16 characters before starting the panel. The panel binds to `0.0.0.0` by default and uses `WEB_PANEL_PORT`, `WEBUI_PORT`, `WEB_PORT`, `PANEL_PORT`, or `PORT`, then `8080` as its port. Allocate that port in Pterodactyl's server settings and open the server address with that port in a browser.

The panel and bot both use the configured database engine. `DB_ENGINE=sqlite` is the default. For MySQL or MariaDB, use `DB_ENGINE=mysql` and provide `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, and `DB_PASSWORD`; optional TLS is enabled by `DB_TLS=1` and verifies the server certificate. `DB_TLS_CA` can point to a custom CA certificate. The MySQL driver is included in `requirements.txt` and the project dependencies.

`BOT_HEALTH_PORT` starts a separate `/healthz` endpoint in the bot process; it returns success only after Discord reports the bot ready. On startup, the bot publishes its application commands globally; Discord may take up to an hour to show newly added commands. When `DEV_GUILD_ID` is set, startup clears server-scoped command copies in that server so they do not duplicate the bot's global commands. `<prefix>sync dev` can create temporary guild copies for fast testing, and `<prefix>sync clear` removes them. `LOG_LEVEL` sets console logging detail.

The panel can edit welcome channel IDs, titles, messages, and colors, review suggestion statuses and notes, and configure support tickets. Its server selector shows the saved Discord server names and remembers the last selected server in a browser cookie. Ticket settings include a public panel channel, a log channel, comma-separated admin role IDs, and one category per line in the form `emoji | title | description | optional Discord category channel ID`. The bot refreshes the configured ticket panel automatically. Ticket channels are private to their creator, configured admin roles, and the bot; close actions record the reason and closer in the log channel.

If the panel is reachable over the internet, put it behind HTTPS. For an HTTPS reverse proxy, set `WEBUI_PUBLIC_ORIGIN` to the exact browser origin (for example, `https://bot.example.com`) and set `SECURE_WEB_COOKIE=1`. Do not send the management password over plain HTTP. The panel prints a warning when secure cookies are disabled.

The Pterodactyl startup command can launch the panel with `python webpanel.py &` before starting the bot. The included `requirements.txt` ensures the panel's `aiohttp` dependency is installed by the supplied startup flow.

### Restart Pterodactyl after pushes

`.github/workflows/pterodactyl-restart.yml` sends a restart signal to the Pterodactyl Client API after each push to `main` and can also be run manually from GitHub Actions. Add these repository Actions secrets under **Settings → Secrets and variables → Actions**:

- `PTERODACTYL_URL` — the base URL of the Pterodactyl panel (for example, `https://panel.example.com`), not the bot's allocated game server address.
- `PTERODACTYL_SERVER_ID` — the server identifier shown in the panel's server URL.
- `PTERODACTYL_API_KEY` — a Client API key with access to restart that server.

The workflow calls `POST /api/client/servers/{server}/power` with the `restart` signal. The server's startup command should have `AUTO_UPDATE=1` enabled so it pulls the pushed commit during startup.

With SQLite, the app creates its database file next to `bot.py` on startup; keep it to preserve settings and do not commit it. With MySQL or MariaDB, the app stores data in the configured database instead.

## Core features

### Welcome embeds

When a member joins, the bot sends an embed to the configured channel. If no channel is configured, it uses the server's system channel. Members with **Manage Server** can configure the embed with:

- `/welcome channel #channel` — choose the destination; omit the channel to use the system channel.
- `/welcome message text` — set the embed description.
- `/welcome title text` — set the embed title.
- `/welcome color hex_color` — choose a six-digit color such as `#FF9ECF`.
- `/welcome preview` — preview the current embed privately.

Message and title text support `{mention}`, `{server}`, `{member_count}`, and `{username}` placeholders. For example: `/welcome message Welcome {mention} to {server}! You're member #{member_count}.`

### Support tickets

Configure the panel and log channel in the management panel. Add categories one per line using `emoji | title | description | optional Discord category channel ID`; the last field can place that ticket type under a Discord category. The bot posts or refreshes the ticket dropdown in the selected panel channel automatically; an owner, administrator, or configured admin can run `/panel` to send or refresh it immediately. Members can open one ticket at a time. Each ticket is private to the opener and configured admin roles, pings those roles and the opener, and has a close button with reasons. Closed tickets can be reopened for five minutes; otherwise the bot deletes the channel. Opening, closing, reopening, and automatic deletion are sent to the configured log channel. The bot needs **Manage Channels**, **View Channel**, **Send Messages**, and **Embed Links** permissions in the panel, log, and ticket areas.

### Group calls

`/groupcall create [name]` creates a private voice room for the caller. Right-click a server member and choose **Apps → Add to Group Call** to let them join. Use `/groupcall close` while connected to close the room; it is also deleted automatically after remaining empty for one minute. The bot needs **Manage Channels** permission. The application command and user context menu are published globally at startup, so new installs may take up to an hour to show them.

### Moderation

Moderators with the corresponding Discord permissions can use `/ban`, `/kick`, `/unban`, `/timeout`, and `/untimeout`. Timeouts accept a duration from 1 minute up to 28 days. Commands check role hierarchy and provide a reason where applicable.

### Utilities

- `<prefix>ping` and `/ping` show the bot's latency.
- `<prefix>userinfo [member]` and `/userinfo [member]` show account age, server join time, roles, and member ID. Leave the member blank to view yourself.
- `<prefix>serverinfo` and `/serverinfo` show the server owner, member count, creation date, channel counts, roles, and boost level.
- `<prefix>avatar [user]` and `/avatar [user]` show a profile picture at full size; omit the user to show your own.
- `<prefix>roleinfo <role>` and `/roleinfo <role>` show the role's ID, member count, creation date, and display settings.
- `/poll question choices` (also available as `<prefix>poll "question" choices`) creates a reaction poll. Separate 2–10 choices with `|`, for example `/poll question:"Pizza or sushi?" choices:"Pizza | Sushi | Both"`.
- `<prefix>prefix` shows this server's prefix. The server owner, a Discord administrator, or a member with the configured admin role can change it with `<current prefix>prefix set <new prefix>`. Prefixes are per-server, allow 1–5 printable characters without spaces, and default to `!`.
- `<prefix>sync dev` copies the current application commands to this server for fast testing. It can show duplicate entries while global commands also exist. Use `<prefix>sync clear` to remove this server's copies when testing is done; global commands remain available. `<prefix>sync global` publishes the current application commands globally and may take up to an hour to appear. Bare `<prefix>sync` is an alias for `<prefix>sync clear`. Only the server owner, Discord administrators, and configured admin role can use sync commands.

### Feature suggestions

- `/suggest idea` lets any server member submit a feature idea. Suggestions are saved in the local SQLite database and scoped to the server they came from.
- `/suggestions` privately lists suggestions and their statuses, newest first, eight per page. Add the `page` option to browse older suggestions. Only the server owner, Discord administrators, and configured admin role can review them.
- `/mysuggestions` privately lists your own submissions and statuses.
- `/suggestion-status` lets authorized reviewers mark a suggestion pending, approved, denied, or implemented. Setting a suggestion to denied sends its author a private DM, including an optional reviewer note. If the author has DMs disabled, the command reports that the notification could not be delivered.
- `/features` publicly lists built-in user-facing capabilities and community suggestions marked approved or implemented. It covers moderation, welcome embeds, support tickets, group calls, ping, and suggestions; admin-only maintenance commands are excluded.

## Project layout

- `bot.py` — bot startup, intents, SQLite initialization, and cog loading.
- `database.py` — per-server settings, ticket configuration, and ticket and group call records.
- `checks.py` — shared bot-admin permission check.
- `config.py` — environment configuration.
- `cogs/` — welcome, moderation, utility, suggestion, support ticket, and group call features.
