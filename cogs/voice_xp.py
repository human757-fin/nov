"""Message, voice, and video XP with member ranks and a server leaderboard."""

import logging
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import database

log = logging.getLogger(__name__)
XP_INTERVAL_MINUTES = 5
AUDIO_XP_PER_INTERVAL = 10
VIDEO_XP_PER_INTERVAL = 5
MESSAGE_XP_COOLDOWN_SECONDS = 60


def can_manage_xp():
    async def predicate(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        user = interaction.user
        if guild is None:
            return False
        if user.id == guild.owner_id or user.guild_permissions.administrator:
            return True
        return config.ADMIN_ROLE_ID != 0 and any(
            role.id == config.ADMIN_ROLE_ID for role in getattr(user, "roles", [])
        )

    return app_commands.check(predicate)


class VoiceXP(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.award_voice_activity.start()

    async def cog_unload(self):
        self.award_voice_activity.cancel()

    @tasks.loop(minutes=XP_INTERVAL_MINUTES)
    async def award_voice_activity(self):
        for guild in self.bot.guilds:
            if not database.get_xp_settings(guild.id)["enabled"]:
                continue
            for channel in guild.voice_channels:
                members = [member for member in channel.members if not member.bot]
                if len(members) < 2 or channel == guild.afk_channel:
                    continue
                for member in members:
                    state = member.voice
                    if state is None or state.deaf or state.self_deaf:
                        continue
                    try:
                        result = database.award_voice_xp(
                            guild.id,
                            member.id,
                            audio=AUDIO_XP_PER_INTERVAL,
                            cooldown_seconds=XP_INTERVAL_MINUTES * 60,
                            now=int(discord.utils.utcnow().timestamp()),
                            video=(
                                VIDEO_XP_PER_INTERVAL
                                if state.self_video or state.self_stream
                                else 0
                            ),
                        )
                    except Exception:  # noqa: BLE001
                        log.exception(
                            "Could not award voice XP to member %s in guild %s",
                            member.id,
                            guild.id,
                        )
                        continue
                    if result is None:
                        continue
                    if result["leveled_up"]:
                        await self._handle_level_up(guild, member, result["level"])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if not message.content.strip() and not message.attachments:
            return
        context = await self.bot.get_context(message)
        if (
            context.valid
            or (context.prefix and message.content.startswith(context.prefix))
            or not database.get_xp_settings(message.guild.id)["enabled"]
        ):
            return
        try:
            result = database.award_message_xp(
                message.guild.id,
                message.author.id,
                amount=random.randint(15, 25),
                cooldown_seconds=MESSAGE_XP_COOLDOWN_SECONDS,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "Could not award message XP to member %s in guild %s",
                message.author.id,
                message.guild.id,
            )
            return
        if result is not None and result["leveled_up"]:
            await self._handle_level_up(message.guild, message.author, result["level"])

    @award_voice_activity.before_loop
    async def before_award_voice_activity(self):
        await self.bot.wait_until_ready()

    async def _handle_level_up(
        self, guild: discord.Guild, member: discord.Member, level: int
    ):
        rewards = database.get_xp_role_rewards(guild.id, up_to_level=level)
        reward_roles = [
            guild.get_role(item["role_id"])
            for item in rewards
            if guild.get_role(item["role_id"]) is not None
        ]
        missing_roles = [role for role in reward_roles if role not in member.roles]
        granted_roles = []
        if missing_roles and guild.me and guild.me.guild_permissions.manage_roles:
            eligible = [role for role in missing_roles if role < guild.me.top_role]
            if eligible:
                try:
                    await member.add_roles(
                        *eligible,
                        reason=f"Reached XP level {level}",
                    )
                    granted_roles = eligible
                except discord.HTTPException:
                    log.exception(
                        "Could not grant XP reward roles to member %s in guild %s",
                        member.id,
                        guild.id,
                    )

        settings = database.get_xp_settings(guild.id)
        channel = (
            guild.get_channel(settings["announce_channel_id"])
            if settings["announce_channel_id"]
            else guild.system_channel
        )
        if channel is None or not hasattr(channel, "send"):
            return
        role_note = ""
        if granted_roles:
            reward_names = ", ".join(
                discord.utils.escape_markdown(role.name) for role in granted_roles
            )
            role_note = f"\n\nReward unlocked: {reward_names}."
        try:
            embed = discord.Embed(
                title="🎉 Level Up!",
                description=(
                    f"Congratulations, **{discord.utils.escape_markdown(member.display_name)}**! "
                    f"You reached level **{level}**.{role_note}"
                ),
                color=member.color if member.color.value else discord.Color.blurple(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(
                content=member.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=[member], roles=False, everyone=False
                ),
            )
        except discord.HTTPException:
            log.debug("Could not announce level-up in guild %s", guild.id)

    @commands.hybrid_command(name="rank", description="Check your or another member's XP rank")
    @app_commands.guild_only()
    @commands.guild_only()
    async def rank(
        self, ctx: commands.Context, member: discord.Member | None = None
    ):
        target = member or ctx.author
        stats = database.get_voice_xp(ctx.guild.id, target.id)
        level = stats["level"]
        next_level_total = (level + 1) ** 2 * 100
        remaining = next_level_total - stats["total_xp"]
        progress_start = level**2 * 100
        progress = stats["total_xp"] - progress_start
        progress_needed = next_level_total - progress_start
        embed = discord.Embed(
            title=f"{target.display_name}'s Rank",
            description=(
                f"**Level {level}** · {stats['total_xp']:,} total XP\n"
                f"{progress:,}/{progress_needed:,} XP to Level {level + 1} "
                f"({remaining:,} remaining)"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Message XP", value=f"{stats['message_xp']:,}")
        embed.add_field(name="Audio XP", value=f"{stats['audio_xp']:,}")
        embed.add_field(name="Video XP", value=f"{stats['video_xp']:,}")
        await ctx.send(embed=embed, ephemeral=ctx.interaction is not None)

    @commands.hybrid_command(
        name="leaderboard",
        aliases=["voice-leaderboard"],
        description="Show the server's top XP members",
    )
    @app_commands.guild_only()
    @commands.guild_only()
    async def leaderboard(self, ctx: commands.Context):
        entries = database.get_voice_xp_leaderboard(ctx.guild.id, limit=10)
        if not entries:
            return await ctx.send(
                "No XP yet. Chat or spend time in a voice channel with another member to earn it.",
                ephemeral=ctx.interaction is not None,
            )
        lines = []
        for place, entry in enumerate(entries, start=1):
            member = ctx.guild.get_member(entry["user_id"])
            name = (
                discord.utils.escape_markdown(member.display_name)
                if member
                else f"Former member ({entry['user_id']})"
            )
            lines.append(
                f"**{place}.** {name} — Level {entry['level']} · "
                f"{entry['total_xp']:,} XP"
            )
        embed = discord.Embed(
            title="XP Leaderboard",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @app_commands.command(name="xp-toggle", description="Enable or disable server XP")
    @app_commands.describe(enabled="Whether XP should be enabled")
    @app_commands.guild_only()
    @can_manage_xp()
    async def xp_toggle(self, interaction: discord.Interaction, enabled: bool):
        database.set_xp_enabled(interaction.guild_id, enabled)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"XP earning is now **{state}** in this server.", ephemeral=True
        )

    @app_commands.command(name="xp-announcements", description="Set the level-up announcement channel")
    @app_commands.describe(channel="Leave empty to use the server's system channel")
    @app_commands.guild_only()
    @can_manage_xp()
    async def xp_announcements(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        bot_member = interaction.guild.me
        if channel is not None and (
            bot_member is None
            or not channel.permissions_for(bot_member).send_messages
        ):
            return await interaction.response.send_message(
                "I need permission to send messages in that channel.", ephemeral=True
            )
        database.set_xp_announce_channel(
            interaction.guild_id, channel.id if channel else None
        )
        destination = channel.mention if channel else "the server's system channel"
        await interaction.response.send_message(
            f"Level-up announcements will use {destination}.", ephemeral=True
        )

    @app_commands.command(name="xp-reward", description="Give a role to members when they reach a level")
    @app_commands.describe(level="Level required (1–10000)", role="Role awarded at this level")
    @app_commands.guild_only()
    @can_manage_xp()
    async def xp_reward(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 10000],
        role: discord.Role,
    ):
        bot_member = interaction.guild.me
        if role.is_default() or role.managed or bot_member is None or role >= bot_member.top_role:
            return await interaction.response.send_message(
                "Choose a regular role below my highest role.", ephemeral=True
            )
        try:
            database.set_xp_role_reward(interaction.guild_id, level, role.id)
        except Exception:  # noqa: BLE001
            log.exception("Could not save XP reward for guild %s", interaction.guild_id)
            return await interaction.response.send_message(
                "That role may already be assigned as a reward at another level.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            f"Members will receive {role.mention} when they reach level {level}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="xp-reward-remove", description="Remove a level role reward")
    @app_commands.describe(level="Level whose reward should be removed")
    @app_commands.guild_only()
    @can_manage_xp()
    async def xp_reward_remove(
        self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 10000]
    ):
        removed = database.remove_xp_role_reward(interaction.guild_id, level)
        message = (
            f"Removed the role reward for level {level}."
            if removed
            else f"There was no role reward configured for level {level}."
        )
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="xp-settings", description="View this server's XP settings and rewards")
    @app_commands.guild_only()
    @can_manage_xp()
    async def xp_settings(self, interaction: discord.Interaction):
        settings = database.get_xp_settings(interaction.guild_id)
        channel = (
            interaction.guild.get_channel(settings["announce_channel_id"])
            if settings["announce_channel_id"]
            else interaction.guild.system_channel
        )
        reward_lines = []
        for reward in database.get_xp_role_rewards(interaction.guild_id):
            role = interaction.guild.get_role(reward["role_id"])
            line = f"Level {reward['level']}: {role.mention if role else 'deleted role'}"
            if sum(len(item) + 1 for item in reward_lines) + len(line) > 950:
                reward_lines.append("…and more rewards")
                break
            reward_lines.append(line)
        embed = discord.Embed(title="XP Settings", color=discord.Color.blurple())
        embed.add_field(name="XP earning", value="Enabled" if settings["enabled"] else "Disabled")
        embed.add_field(
            name="Announcements",
            value=channel.mention if channel else "No available channel",
        )
        embed.add_field(
            name="Level role rewards",
            value="\n".join(reward_lines) if reward_lines else "None configured",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CheckFailure):
            message = "Only the server owner, administrators, or the configured bot admin role can change XP settings."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        else:
            raise error


async def setup(bot):
    await bot.add_cog(VoiceXP(bot))
