"""Voice attendance XP, video bonuses, member ranks, and server leaderboard."""

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database

log = logging.getLogger(__name__)
XP_INTERVAL_MINUTES = 5
AUDIO_XP_PER_INTERVAL = 10
VIDEO_XP_PER_INTERVAL = 5


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
                    if result["leveled_up"] and guild.system_channel is not None:
                        try:
                            await guild.system_channel.send(
                                f"🎉 **{discord.utils.escape_markdown(member.display_name)}** "
                                f"reached voice Level {result['level']}!"
                            )
                        except discord.HTTPException:
                            log.debug(
                                "Could not announce voice level-up in guild %s",
                                guild.id,
                            )

    @award_voice_activity.before_loop
    async def before_award_voice_activity(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="rank", description="Check your or another member's voice XP rank")
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
            title=f"{target.display_name}'s Voice Rank",
            description=(
                f"**Level {level}** · {stats['total_xp']:,} total XP\n"
                f"{progress:,}/{progress_needed:,} XP to Level {level + 1} "
                f"({remaining:,} remaining)"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Audio XP", value=f"{stats['audio_xp']:,}")
        embed.add_field(name="Video XP", value=f"{stats['video_xp']:,}")
        await ctx.send(embed=embed, ephemeral=ctx.interaction is not None)

    @commands.hybrid_command(name="voice-leaderboard", description="Show the server's top voice XP members")
    @app_commands.guild_only()
    @commands.guild_only()
    async def leaderboard(self, ctx: commands.Context):
        entries = database.get_voice_xp_leaderboard(ctx.guild.id, limit=10)
        if not entries:
            return await ctx.send(
                "No voice XP yet. Spend time in a voice channel with another member to earn it.",
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
            title="Voice XP Leaderboard",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(VoiceXP(bot))
