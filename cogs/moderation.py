# cogs/moderation.py
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ban", description="Ban a member from the server")
    @app_commands.describe(member="Who to ban", reason="Why they're being banned")
    @app_commands.default_permissions(
        ban_members=True
    )  # hides it from non-mods in the UI
    @app_commands.checks.has_permissions(ban_members=True)  # enforces it
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason given",
    ):
        # Safety checks
        if member == interaction.user:
            return await interaction.response.send_message(
                "You can't ban yourself.", ephemeral=True
            )
        if (
            member.top_role >= interaction.user.top_role
            and interaction.user != interaction.guild.owner
        ):
            return await interaction.response.send_message(
                "You can't ban someone with an equal or higher role.", ephemeral=True
            )
        if member.top_role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "My role is too low to ban that person.", ephemeral=True
            )

        await member.ban(reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(
            f"Banned **{member}**. Reason: {reason}"
        )

    @app_commands.command(name="kick", description="Kick a member from the server")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason given",
    ):
        if member == interaction.user:
            return await interaction.response.send_message(
                "You can't kick yourself.", ephemeral=True
            )
        if (
            member.top_role >= interaction.user.top_role
            and interaction.user != interaction.guild.owner
        ):
            return await interaction.response.send_message(
                "You can't kick someone with an equal or higher role.", ephemeral=True
            )
        if member.top_role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "My role is too low to kick that person.", ephemeral=True
            )
        await member.kick(reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(
            f"Kicked **{member}**. Reason: {reason}"
        )

    @app_commands.command(name="unban", description="Unban a user by ID")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user)
            await interaction.response.send_message(f"Unbanned **{user}**.")
        except ValueError, discord.NotFound:
            await interaction.response.send_message(
                "Couldn't find a banned user with that ID.", ephemeral=True
            )

    @app_commands.command(
        name="timeout", description="Temporarily prevent a member from chatting"
    )
    @app_commands.describe(
        member="Who to time out",
        minutes="Duration from 1 minute to 28 days",
        reason="Why they're being timed out",
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        reason: str = "No reason given",
    ):
        if member == interaction.user:
            return await interaction.response.send_message(
                "You can't time yourself out.", ephemeral=True
            )
        if member == interaction.guild.owner:
            return await interaction.response.send_message(
                "You can't time out the server owner.", ephemeral=True
            )
        if (
            member.top_role >= interaction.user.top_role
            and interaction.user != interaction.guild.owner
        ):
            return await interaction.response.send_message(
                "You can't time out someone with an equal or higher role.",
                ephemeral=True,
            )
        if member.top_role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "My role is too low to time out that person.", ephemeral=True
            )

        until = datetime.now(UTC) + timedelta(minutes=minutes)
        await member.timeout(until, reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(
            f"Timed out **{member}** for {minutes} minute(s). Reason: {reason}"
        )

    @app_commands.command(name="untimeout", description="Remove a member's timeout")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        if member == interaction.guild.owner:
            return await interaction.response.send_message(
                "You can't edit the server owner's timeout.", ephemeral=True
            )
        if (
            member.top_role >= interaction.user.top_role
            and interaction.user != interaction.guild.owner
        ):
            return await interaction.response.send_message(
                "You can't edit someone with an equal or higher role.", ephemeral=True
            )
        if member.top_role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "My role is too low to edit that member.", ephemeral=True
            )
        await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
        await interaction.response.send_message(
            f"Removed the timeout for **{member}**."
        )

    # Handle permission errors nicely
    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You don't have permission to use that.", ephemeral=True
            )
        else:
            raise error


async def setup(bot):
    await bot.add_cog(Moderation(bot))
