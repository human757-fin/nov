"""Temporary private voice rooms with a user context-menu invite."""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import database

log = logging.getLogger(__name__)


class GroupCalls(
    commands.GroupCog,
    group_name="groupcall",
    group_description="Create or close a temporary private voice room",
):
    def __init__(self, bot):
        self.bot = bot
        self.cleanup_tasks: dict[int, asyncio.Task] = {}

    @app_commands.command(name="create", description="Create a private group voice room")
    @app_commands.describe(name="Name for the temporary voice room")
    @app_commands.guild_only()
    async def create_room(
        self, interaction: discord.Interaction, name: str = "group-call"
    ):
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "Use this command in a server.", ephemeral=True
            )
        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_channels:
            return await interaction.response.send_message(
                "I need the Manage Channels permission to create group call rooms.",
                ephemeral=True,
            )
        existing_record = database.get_group_call_room(
            guild.id, owner_id=interaction.user.id
        )
        existing = (
            guild.get_channel(existing_record["channel_id"])
            if existing_record
            else None
        )
        if existing is not None:
            return await interaction.response.send_message(
                f"You already have a group call room: {existing.mention}", ephemeral=True
            )
        if existing_record:
            database.delete_group_call_room(existing_record["channel_id"])

        safe_name = " ".join(name.split())[:90] or "group-call"
        overwrites = {
            role: discord.PermissionOverwrite(view_channel=False, connect=False)
            for role in guild.roles
            if role == guild.default_role
            or role.permissions.view_channel
            or role.permissions.connect
        }
        overwrites.update(
            {
                interaction.user: discord.PermissionOverwrite(
                    view_channel=True, connect=True
                ),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True, connect=True, manage_channels=True
                ),
            }
        )
        if len(overwrites) > 100:
            return await interaction.response.send_message(
                "This server has too many roles with voice access for me to make a private room safely.",
                ephemeral=True,
            )
        try:
            await interaction.response.defer(ephemeral=True)
            channel = await guild.create_voice_channel(
                name=safe_name,
                overwrites=overwrites,
                reason=f"Temporary group call created by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "I couldn't create the room. Check my channel permissions and role position.",
                ephemeral=True,
            )
        database.create_group_call_room(guild.id, channel.id, interaction.user.id)
        self._schedule_cleanup(guild.id, channel.id)
        await interaction.followup.send(
            f"Created {channel.mention}. Join it, then right-click someone and choose "
            "**Apps → Add to Group Call** to invite them. The room is deleted after it "
            "stays empty for one minute.",
            ephemeral=True,
        )

    @app_commands.command(name="close", description="Close your group call room")
    @app_commands.guild_only()
    async def close_room(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        voice = getattr(member, "voice", None)
        channel = voice.channel if voice else None
        if not isinstance(channel, discord.VoiceChannel):
            return await interaction.response.send_message(
                "Join your group call room before closing it.", ephemeral=True
            )
        room = database.get_group_call_room(guild.id, channel_id=channel.id)
        if room is None:
            return await interaction.response.send_message(
                "This voice channel is not a bot-created group call room.", ephemeral=True
            )
        owner_id = room["owner_id"]
        if owner_id != member.id and not getattr(
            member.guild_permissions, "manage_channels", False
        ):
            return await interaction.response.send_message(
                "Only the room creator or a server manager can close this room.",
                ephemeral=True,
            )
        await interaction.response.send_message("Closing the group call room.", ephemeral=True)
        await channel.delete(reason=f"Group call closed by {member} ({member.id})")
        database.delete_group_call_room(channel.id)

    async def add_member_to_group_call(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "Use this action in a server.", ephemeral=True
            )
        if user.id == member.id:
            return await interaction.response.send_message(
                "You're already the room creator.", ephemeral=True
            )
        room = database.get_group_call_room(guild.id, owner_id=member.id)
        channel = guild.get_channel(room["channel_id"]) if room else None
        if not isinstance(channel, discord.VoiceChannel):
            return await interaction.response.send_message(
                "Create a group call room first with `/groupcall create`.", ephemeral=True
            )
        if user.guild.id != guild.id:
            return await interaction.response.send_message(
                "That person is no longer in this server.", ephemeral=True
            )
        try:
            await interaction.response.defer(ephemeral=True)
            await channel.set_permissions(
                user,
                view_channel=True,
                connect=True,
                reason=f"Invited to group call by {member} ({member.id})",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "I couldn't invite them. Check my channel permissions and role position.",
                ephemeral=True,
            )
        await interaction.followup.send(
            f"Added {user.mention} to {channel.mention}. They can join whenever they're ready.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def cog_load(self):
        self.add_member_context = app_commands.ContextMenu(
            name="Add to Group Call", callback=self.add_member_to_group_call
        )
        self.bot.tree.add_command(self.add_member_context)

    async def cog_unload(self):
        for task in self.cleanup_tasks.values():
            task.cancel()
        self.cleanup_tasks.clear()
        self.bot.tree.remove_command(
            "Add to Group Call", type=discord.AppCommandType.user
        )

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for room in database.get_group_call_rooms(guild.id):
                channel = guild.get_channel(room["channel_id"])
                if channel is None:
                    database.delete_group_call_room(room["channel_id"])
                elif (
                    isinstance(channel, discord.VoiceChannel)
                    and not channel.members
                    and channel.id not in self.cleanup_tasks
                ):
                    self._schedule_cleanup(guild.id, channel.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if before.channel == after.channel:
            return
        if after.channel is not None:
            joined_room = database.get_group_call_room(
                member.guild.id, channel_id=after.channel.id
            )
            if joined_room is not None:
                task = self.cleanup_tasks.pop(after.channel.id, None)
                if task is not None:
                    task.cancel()
        channel = before.channel
        if channel is None:
            return
        room = database.get_group_call_room(member.guild.id, channel_id=channel.id)
        if room is None or channel.members:
            return
        self._schedule_cleanup(member.guild.id, channel.id)

    def _schedule_cleanup(self, guild_id: int, channel_id: int):
        old_task = self.cleanup_tasks.pop(channel_id, None)
        if old_task is not None:
            old_task.cancel()
        self.cleanup_tasks[channel_id] = asyncio.create_task(
            self._delete_if_still_empty(guild_id, channel_id)
        )

    async def _delete_if_still_empty(self, guild_id: int, channel_id: int):
        try:
            await asyncio.sleep(60)
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel) or channel.members:
                return
            room = database.get_group_call_room(guild_id, channel_id=channel_id)
            if room is None:
                return
            await channel.delete(reason="Temporary group call was empty for one minute")
            database.delete_group_call_room(channel_id)
        except discord.NotFound:
            database.delete_group_call_room(channel_id)
        except discord.Forbidden:
            log.warning("Could not clean up empty group call channel %s", channel_id)
        finally:
            if self.cleanup_tasks.get(channel_id) is asyncio.current_task():
                self.cleanup_tasks.pop(channel_id, None)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        database.delete_group_call_room(channel.id)


async def setup(bot):
    await bot.add_cog(GroupCalls(bot))
