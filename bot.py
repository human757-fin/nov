# bot.py
from pathlib import Path
import logging

import discord
from aiohttp import web
from discord.ext import commands

import config
import database

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


class Catgirl(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.health_runner: web.AppRunner | None = None

    async def _save_guild_names(self):
        for guild in self.guilds:
            database.update_guild_metadata(guild.id, guild.name)

    async def on_ready(self):
        await self._save_guild_names()

    async def on_guild_join(self, guild: discord.Guild):
        database.update_guild_metadata(guild.id, guild.name)

    async def on_guild_update(self, _before: discord.Guild, after: discord.Guild):
        database.update_guild_metadata(after.id, after.name)

    async def setup_hook(self):
        database.initialize()
        cogs_dir = Path(__file__).parent / "cogs"
        for file in sorted(cogs_dir.glob("*.py")):
            if file.name.startswith("_"):
                continue
            extension = f"cogs.{file.stem}"
            try:
                await self.load_extension(extension)
                print(f"Loaded {extension}")
            except Exception:  # noqa: BLE001
                logging.exception("Failed to load %s", extension)

        try:
            synced = await self.tree.sync()
            logging.info("Published %s global application commands", len(synced))
        except Exception:  # noqa: BLE001
            logging.exception("Failed to publish global application commands")

        if config.DEV_GUILD_ID:
            guild = discord.Object(id=config.DEV_GUILD_ID)
            # Global commands already appear in this server. Guild-scoped
            # copies have the same names and make Discord show duplicates.
            # Clear those copies while leaving the global commands untouched.
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            logging.info(
                "Cleared guild-scoped command copies from development guild %s; global commands remain",
                config.DEV_GUILD_ID,
            )

        if config.BOT_HEALTH_PORT:
            health_app = web.Application()

            async def healthcheck(_request: web.Request) -> web.Response:
                if self.is_ready():
                    return web.Response(text="ok", content_type="text/plain")
                raise web.HTTPServiceUnavailable(
                    text="starting", content_type="text/plain"
                )

            health_app.router.add_get("/", healthcheck)
            health_app.router.add_get("/health", healthcheck)
            health_app.router.add_get("/healthz", healthcheck)
            self.health_runner = web.AppRunner(health_app)
            await self.health_runner.setup()
            site = web.TCPSite(
                self.health_runner, config.BOT_HEALTH_HOST, config.BOT_HEALTH_PORT
            )
            await site.start()
            logging.info(
                "Bot health endpoint listening on %s:%s/healthz",
                config.BOT_HEALTH_HOST,
                config.BOT_HEALTH_PORT,
            )

    async def close(self):
        if self.health_runner is not None:
            await self.health_runner.cleanup()
            self.health_runner = None
        await super().close()


async def get_prefix(_bot: Catgirl, message: discord.Message):
    prefix = (
        database.get_command_prefix(message.guild.id)
        if message.guild is not None
        else database.DEFAULT_PREFIX
    )
    return commands.when_mentioned_or(prefix)(_bot, message)


bot = Catgirl(command_prefix=get_prefix, intents=intents)
bot.run(config.TOKEN)
