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

    @commands.hybrid_command(name="userinfo", description="Show details about a server member")
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show useful account and server membership details."""
        member = member or ctx.author
        embed = discord.Embed(
            title=f"Member information: {member.display_name}",
            color=member.color if member.color.value else discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Account", value=f"{member.mention}\nID: `{member.id}`", inline=False)
        embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown")
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"))
        roles = [role.mention for role in reversed(member.roles) if role != ctx.guild.default_role]
        role_text = ", ".join(roles[:10]) or "No roles"
        if len(roles) > 10:
            role_text += f" and {len(roles) - 10} more"
        embed.add_field(name=f"Roles ({len(roles)})", value=role_text, inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", description="Show details about this server")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context):
        """Show server size, ownership, channels, and creation date."""
        guild = ctx.guild
        embed = discord.Embed(
            title=guild.name,
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>" if guild.owner_id else "Unknown")
        embed.add_field(name="Members", value=f"{guild.member_count:,}")
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "R"))
        embed.add_field(
            name="Channels",
            value=f"Text: {len(guild.text_channels)} · Voice: {len(guild.voice_channels)} · Categories: {len(guild.categories)}",
            inline=False,
        )
        embed.add_field(name="Roles", value=str(max(0, len(guild.roles) - 1)))
        embed.add_field(name="Boosts", value=f"{guild.premium_subscription_count or 0} (level {guild.premium_tier})")
        embed.set_footer(text=f"Server ID: {guild.id}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="avatar", description="Show a user's profile picture")
    async def avatar(self, ctx: commands.Context, user: Optional[discord.User] = None):
        user = user or ctx.author
        embed = discord.Embed(title=f"{user.display_name}'s avatar", color=discord.Color.blurple())
        embed.set_image(url=user.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="roleinfo", description="Show details about a server role")
    @commands.guild_only()
    async def roleinfo(self, ctx: commands.Context, role: discord.Role):
        embed = discord.Embed(title=f"Role information: {role.name}", color=role.color or discord.Color.blurple())
        embed.add_field(name="Role ID", value=f"`{role.id}`")
        embed.add_field(name="Members", value=str(len(role.members)))
        embed.add_field(name="Created", value=discord.utils.format_dt(role.created_at, "R"))
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No")
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No")
        embed.add_field(name="Position", value=str(role.position))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="poll", description="Create a reaction poll (separate choices with |)")
    @commands.guild_only()
    async def poll(self, ctx: commands.Context, question: str, choices: str):
        options = [choice.strip() for choice in choices.split("|") if choice.strip()]
        if len(question) > 250 or not 2 <= len(options) <= 10 or any(len(choice) > 80 for choice in options):
            return await ctx.send(
                "Use a question up to 250 characters and 2–10 choices up to 80 characters each, separated by `|`.",
                ephemeral=ctx.interaction is not None,
            )
        marks = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        description = "\n".join(f"{marks[index]} {choice}" for index, choice in enumerate(options))
        message = await ctx.send(
            embed=discord.Embed(title=question, description=description, color=discord.Color.blurple()),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        for mark in marks[:len(options)]:
            await message.add_reaction(mark)

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
