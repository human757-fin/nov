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

        if config.DEV_GUILD_ID:
            guild = discord.Object(id=config.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logging.info(
                "Synced %s application commands to development guild %s",
                len(synced),
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


bot = Catgirl(command_prefix="!", intents=intents)
bot.run(config.TOKEN)
