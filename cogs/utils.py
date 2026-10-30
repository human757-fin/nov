# cogs/utils.py
from typing import Literal, Optional

import discord
from discord.ext import commands

import database
from checks import is_bot_admin


class Utils(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Works as both /ping and the server's configured prefix command.
    @commands.hybrid_command(name="ping", description="Check the bot's latency")
    async def ping(self, ctx: commands.Context):
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"Pong! `{latency}ms`")

    @commands.group(name="prefix", invoke_without_command=True)
    async def prefix(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("Prefixes are configured separately for each server.")
        current = database.get_command_prefix(ctx.guild.id)
        await ctx.send(
            f"This server's prefix is {current!r}. An admin can change it with "
            f"{current}prefix set <new prefix>."
        )

    @prefix.command(name="set")
    @commands.guild_only()
    @is_bot_admin()
    async def set_prefix(self, ctx: commands.Context, new_prefix: str):
        if (
            not 1 <= len(new_prefix) <= 5
            or not new_prefix.isprintable()
            or any(character.isspace() for character in new_prefix)
        ):
            return await ctx.send("Choose a prefix with 1–5 printable characters and no spaces.")

        database.set_command_prefix(ctx.guild.id, new_prefix)
        await ctx.send(
            f"Prefix changed to {new_prefix!r}. Use {new_prefix}ping to check it.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @set_prefix.error
    async def set_prefix_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(
                "Only the server owner, a server administrator, or the configured admin role can change the prefix."
            )
        else:
            raise error

    # Prefix-only, admin-only: <prefix>sync
    @commands.command(name="sync")
    @is_bot_admin()
    async def sync(
        self,
        ctx: commands.Context,
        scope: Optional[Literal["dev", "global", "clear"]] = "clear",  # noqa: UP045
    ):
        """
        The server's current prefix followed by "sync" clears this server's command copies.
        Add "dev" to sync commands here for fast testing, "global" to publish globally,
        or "clear" to remove this server's copies.
        """
        if scope == "global":
            synced = await self.bot.tree.sync()
            await ctx.send(f"Synced {len(synced)} commands globally.")

        elif ctx.guild is None:
            await ctx.send("Run this command in a server.")

        elif scope == "dev":
            self.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await self.bot.tree.sync(guild=ctx.guild)
            await ctx.send(
                f"Synced {len(synced)} commands to this dev server. "
                f"Use `{ctx.clean_prefix}sync clear` when you're done testing."
            )

        else:  # clear, also the default
            self.bot.tree.clear_commands(guild=ctx.guild)
            await self.bot.tree.sync(guild=ctx.guild)
            await ctx.send(
                "Cleared this server's command copies. Global commands remain available."
            )

    @sync.error
    async def sync_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You don't have permission to use that.")
        else:
            raise error

async def setup(bot):
    await bot.add_cog(Utils(bot))
