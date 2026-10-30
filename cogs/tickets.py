"""Configurable private support tickets with persistent Discord components."""

from __future__ import annotations

import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import database

log = logging.getLogger(__name__)

CLOSE_REASONS = (
    ("resolved", "Resolved"),
    ("answered", "Question answered"),
    ("duplicate", "Duplicate ticket"),
    ("no_response", "No response"),
    ("other", "Other"),
)


def _ticket_embed(categories: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="Create a support ticket",
        description=(
            "Choose the category that best matches your request. "
            "A private channel will be created for you and the support team."
        ),
        color=0x91A8FF,
    )
    lines = []
    for category in categories:
        line = f"{category.get('emoji') or '🎫'} **{category['name']}** — {category['description']}"
        if sum(map(len, lines)) + len(line) + len(lines) * 1 > 3600:
            break
        lines.append(line)
    if lines:
        embed.description += "\n\n" + "\n".join(lines)
    return embed


class TicketCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[dict]):
        options = []
        for category in categories:
            emoji = None
            if category.get("emoji"):
                emoji = category["emoji"]
                if emoji.startswith("<"):
                    try:
                        emoji = discord.PartialEmoji.from_str(emoji)
                    except (TypeError, ValueError):
                        log.warning("Ignoring invalid ticket category emoji for %s", category["name"])
            options.append(
                discord.SelectOption(
                    label=category["name"],
                    value=category["category_key"],
                    description=category["description"],
                    emoji=emoji,
                )
            )
        super().__init__(
            placeholder="Choose a ticket category…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="nov:tickets:create",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _create_ticket(interaction, self.values[0])


def _panel_view(categories: list[dict]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(TicketCategorySelect(categories))
    return view


async def _create_ticket(interaction: discord.Interaction, selected: str) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            "Tickets can only be opened in a server.", ephemeral=True
        )

    config = database.get_ticket_config(interaction.guild.id)
    category = next(
        (item for item in config["categories"] if item["category_key"] == selected),
        None,
    )
    if category is None:
        return await interaction.response.send_message(
            "That ticket category is no longer available. Please use the updated panel.",
            ephemeral=True,
        )

    existing = database.get_open_ticket(interaction.guild.id, interaction.user.id)
    if existing:
        channel = (
            interaction.guild.get_channel(int(existing["channel_id"]))
            if existing.get("channel_id")
            else None
        )
        message = (
            f"You already have an open ticket: {channel.mention}"
            if channel
            else "Your ticket is still being created. Please try again shortly."
        )
        return await interaction.response.send_message(message, ephemeral=True)

    parent = None
    parent_id = category.get("parent_category_id")
    if parent_id:
        parent = interaction.guild.get_channel(int(parent_id))
        if not isinstance(parent, discord.CategoryChannel):
            return await interaction.response.send_message(
                f"The configured Discord category for **{category['name']}** is missing. "
                "Ask a server admin to update the ticket settings.",
                ephemeral=True,
            )

    bot_member = interaction.guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_channels:
        return await interaction.response.send_message(
            "I need the Manage Channels permission to create private tickets.",
            ephemeral=True,
        )

    await interaction.response.defer(ephemeral=True, thinking=True)
    ticket_id = database.create_ticket(
        interaction.guild.id,
        interaction.user.id,
        category["category_key"],
        category["name"],
    )
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
        bot_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
        ),
    }
    admin_roles = []
    for role_id in config["admin_role_ids"]:
        role = interaction.guild.get_role(role_id)
        if role is not None:
            admin_roles.append(role)
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )

    safe_name = re.sub(r"[^a-z0-9-]+", "-", interaction.user.name.lower()).strip("-")
    channel_name = f"ticket-{safe_name[:70] or 'user'}-{ticket_id}"
    try:
        channel = await interaction.guild.create_text_channel(
            channel_name,
            category=parent,
            overwrites=overwrites,
            topic=f"Support ticket #{ticket_id} · {category['name']} · user {interaction.user.id}",
            reason=f"Support ticket opened by {interaction.user} ({interaction.user.id})",
        )
        database.set_ticket_channel(ticket_id, channel.id)
        mentions = " ".join(role.mention for role in admin_roles)
        mentions = f"{mentions} {interaction.user.mention}".strip()
        embed = discord.Embed(
            title=f"{category.get('emoji') or '🎫'} {category['name']}",
            description=(
                f"{interaction.user.mention}, describe your request here. "
                "A support team member will be with you shortly."
            ),
            color=0x91A8FF,
        )
        embed.add_field(name="Ticket", value=f"#{ticket_id}", inline=True)
        embed.add_field(name="Opened by", value=interaction.user.mention, inline=True)
        control_message = await channel.send(
            content=mentions or None,
            embed=embed,
            view=TicketControlsView(interaction.client),
            allowed_mentions=discord.AllowedMentions(
                users=[interaction.user], roles=admin_roles, replied_user=False
            ),
        )
        interaction.client.add_view(
            TicketControlsView(interaction.client), message_id=control_message.id
        )
        await _send_log(
            interaction.guild,
            config,
            title=f"Ticket #{ticket_id} opened",
            description=(
                f"Channel: {channel.mention}\n"
                f"Opened by: {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"Category: {category['name']}"
            ),
            color=0x64D7B0,
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create support ticket in guild %s", interaction.guild.id)
        database.delete_ticket(ticket_id)
        try:
            if "channel" in locals():
                await channel.delete(reason="Ticket setup failed")
        except discord.HTTPException:
            log.exception("Could not clean up failed ticket channel")
        return await interaction.followup.send(
            "I couldn't finish creating the ticket. Check my channel permissions and try again.",
            ephemeral=True,
        )

    await interaction.followup.send(
        f"Your ticket is ready: {channel.mention}", ephemeral=True
    )


class TicketControlsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Close ticket",
        style=discord.ButtonStyle.secondary,
        emoji="🔒",
        custom_id="nov:tickets:close",
    )
    async def close_ticket_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        ticket = database.get_ticket_by_channel(interaction.channel_id)
        if ticket is None or ticket["status"] != "open":
            return await interaction.response.send_message(
                "This channel isn't an open ticket.", ephemeral=True
            )
        if not _can_close(interaction, ticket):
            return await interaction.response.send_message(
                "Only the ticket creator, a configured ticket admin, or a channel manager can close this ticket.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            "Choose a reason for closing this ticket.",
            view=CloseReasonView(self.bot, int(ticket["id"])),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Reopen ticket",
        style=discord.ButtonStyle.success,
        emoji="🔓",
        custom_id="nov:tickets:reopen",
    )
    async def reopen_ticket_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        ticket = database.get_ticket_by_channel(interaction.channel_id)
        if ticket is None or ticket["status"] != "closed":
            return await interaction.response.send_message(
                "This ticket isn't closed.", ephemeral=True
            )
        if not _can_close(interaction, ticket):
            return await interaction.response.send_message(
                "Only the ticket creator, a configured ticket admin, or a channel manager can reopen this ticket.",
                ephemeral=True,
            )
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Ticket channel is unavailable.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        reopened = database.reopen_ticket(int(ticket["id"]), interaction.user.id)
        if reopened is None:
            return await interaction.followup.send(
                "This ticket can no longer be reopened.", ephemeral=True
            )
        creator = guild.get_member(int(ticket["user_id"])) or discord.Object(
            id=int(ticket["user_id"])
        )
        try:
            await channel.set_permissions(
                creator,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                reason=f"Ticket reopened by {interaction.user}",
            )
            if channel.name.startswith("closed-"):
                await channel.edit(
                    name=channel.name.removeprefix("closed-"),
                    reason=f"Ticket reopened by {interaction.user}",
                )
        except discord.HTTPException:
            log.exception("Could not restore access to ticket channel %s", channel.id)
            database.close_ticket(int(ticket["id"]), interaction.user.id, "Reopen failed")
            return await interaction.followup.send(
                "I couldn't reopen this ticket. Check my channel permissions and try again.",
                ephemeral=True,
            )

        try:
            await channel.send(
                f"🔓 Ticket reopened by {interaction.user.mention}.",
                allowed_mentions=discord.AllowedMentions(users=[interaction.user]),
            )
        except discord.HTTPException:
            log.exception("Could not post ticket reopen notice in channel %s", channel.id)
        config = database.get_ticket_config(guild.id)
        await _send_log(
            guild,
            config,
            title=f"Ticket #{ticket['id']} reopened",
            description=(
                f"Channel: {channel.mention}\n"
                f"Reopened by: {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"Category: {ticket['category_name']}"
            ),
            color=0x64D7B0,
        )
        await interaction.followup.send(
            "Ticket reopened. The deletion timer has been cancelled.", ephemeral=True
        )


def _can_close(interaction: discord.Interaction, ticket: dict) -> bool:
    if interaction.user.id == int(ticket["user_id"]):
        return True
    if isinstance(interaction.user, discord.Member):
        if interaction.user.guild_permissions.manage_channels:
            return True
        config = database.get_ticket_config(interaction.guild_id)
        configured = set(config["admin_role_ids"])
        return any(role.id in configured for role in interaction.user.roles)
    return False


class CloseReasonSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, ticket_id: int):
        self.bot = bot
        self.ticket_id = ticket_id
        super().__init__(
            placeholder="Select a close reason…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=label, value=value)
                for value, label in CLOSE_REASONS
            ],
            custom_id="nov:tickets:close-reason-temporary",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        reason = dict(CLOSE_REASONS)[self.values[0]]
        await interaction.response.defer(ephemeral=True, thinking=True)
        ticket = database.get_ticket_by_channel(interaction.channel_id)
        if ticket is None or int(ticket["id"]) != self.ticket_id:
            return await interaction.followup.send("Ticket record not found.", ephemeral=True)
        if ticket["status"] != "open":
            return await interaction.followup.send("This ticket was already closed.", ephemeral=True)
        if not _can_close(interaction, ticket):
            return await interaction.followup.send("You can't close this ticket.", ephemeral=True)
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send("Ticket channel is unavailable.", ephemeral=True)

        creator = guild.get_member(int(ticket["user_id"])) or discord.Object(
            id=int(ticket["user_id"])
        )
        try:
            await channel.set_permissions(
                creator,
                view_channel=True,
                send_messages=False,
                read_message_history=True,
                reason=f"Ticket closed by {interaction.user} · {reason}",
            )
            await channel.edit(
                name=(channel.name if channel.name.startswith("closed-") else f"closed-{channel.name}")[:100],
                reason=f"Ticket closed by {interaction.user} · {reason}",
            )
        except discord.HTTPException:
            log.exception("Could not lock ticket channel %s", channel.id)
            return await interaction.followup.send(
                "I couldn't lock this ticket. Check my channel permissions and try again.",
                ephemeral=True,
            )

        closed = database.close_ticket(self.ticket_id, interaction.user.id, reason)
        if closed is None:
            return await interaction.followup.send("This ticket was already closed.", ephemeral=True)
        await channel.send(
            f"🔒 Ticket closed by {interaction.user.mention}. Reason: **{reason}**. "
            "Use the Reopen ticket button within 5 minutes to keep it open.",
            allowed_mentions=discord.AllowedMentions(users=[interaction.user]),
        )
        config = database.get_ticket_config(guild.id)
        await _send_log(
            guild,
            config,
            title=f"Ticket #{self.ticket_id} closed",
            description=(
                f"Channel: {channel.mention}\n"
                f"Opened by: <@{ticket['user_id']}> (`{ticket['user_id']}`)\n"
                f"Closed by: {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"Category: {ticket['category_name']}\nReason: {reason}"
            ),
            color=0xFF8A9B,
        )
        await interaction.followup.send("Ticket closed and logged.", ephemeral=True)


class CloseReasonView(discord.ui.View):
    def __init__(self, bot: commands.Bot, ticket_id: int):
        super().__init__(timeout=180)
        self.add_item(CloseReasonSelect(bot, ticket_id))


async def _send_log(
    guild: discord.Guild,
    config: dict,
    *,
    title: str,
    description: str,
    color: int,
) -> None:
    log_channel_id = config.get("log_channel_id")
    if not log_channel_id:
        return
    channel = guild.get_channel(int(log_channel_id))
    if not isinstance(channel, discord.TextChannel):
        log.warning("Ticket log channel %s is unavailable in guild %s", log_channel_id, guild.id)
        return
    try:
        await channel.send(
            embed=discord.Embed(title=title, description=description, color=color),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        log.exception("Could not write ticket log in channel %s", channel.id)


async def _can_manage_panel(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        return False
    if member.id == guild.owner_id or member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_guild:
        return True
    ticket_config = database.get_ticket_config(guild.id)
    admin_role_ids = set(ticket_config["admin_role_ids"])
    if config.ADMIN_ROLE_ID:
        admin_role_ids.add(config.ADMIN_ROLE_ID)
    return any(role.id in admin_role_ids for role in member.roles)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced_panel_state: dict[int, tuple] = {}
        self.sync_ticket_panels.start()
        self.cleanup_closed_tickets.start()

    async def cog_unload(self):
        self.sync_ticket_panels.cancel()
        self.cleanup_closed_tickets.cancel()

    @tasks.loop(seconds=60)
    async def sync_ticket_panels(self):
        for guild in self.bot.guilds:
            config = database.get_ticket_config(guild.id)
            await self._sync_guild_panel(guild, config)

    async def _sync_guild_panel(self, guild: discord.Guild, config: dict) -> bool:
        channel_id = config.get("panel_channel_id")
        message_id = config.get("panel_message_id")
        categories = config.get("categories", [])
        state = (
            channel_id,
            message_id,
            tuple(
                (item["category_key"], item["name"], item["emoji"], item["description"], item["parent_category_id"])
                for item in categories
            ),
        )
        if self._synced_panel_state.get(guild.id) == state:
            return True
        if not channel_id:
            return False
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            log.warning("Ticket panel channel %s is unavailable in guild %s", channel_id, guild.id)
            return False
        message = None
        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                message = None
            except discord.HTTPException:
                log.exception("Could not fetch ticket panel %s", message_id)
                return False
        try:
            if not categories:
                if message is not None:
                    await message.delete()
                database.set_ticket_panel_message(guild.id, None)
                self._synced_panel_state[guild.id] = state
                return False
            view = _panel_view(categories)
            embed = _ticket_embed(categories)
            if message is None:
                message = await channel.send(embed=embed, view=view)
                database.set_ticket_panel_message(guild.id, message.id)
            else:
                await message.edit(embed=embed, view=view)
            self.bot.add_view(_panel_view(categories), message_id=message.id)
            self._synced_panel_state[guild.id] = (
                channel_id,
                message.id,
                state[2],
            )
            return True
        except discord.HTTPException:
            log.exception("Could not update ticket panel for guild %s", guild.id)
            return False

    @sync_ticket_panels.before_loop
    async def before_sync_ticket_panels(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def cleanup_closed_tickets(self):
        now = int(time.time())
        for ticket in database.get_expired_tickets(now):
            guild = self.bot.get_guild(int(ticket["guild_id"]))
            if guild is None:
                continue
            if ticket["status"] == "closed" and not database.claim_ticket_deletion(
                int(ticket["id"]), now
            ):
                continue

            channel = guild.get_channel(int(ticket["channel_id"])) if ticket.get("channel_id") else None
            if channel is None and ticket.get("channel_id"):
                try:
                    channel = await guild.fetch_channel(int(ticket["channel_id"]))
                except discord.NotFound:
                    channel = None
                except discord.HTTPException:
                    log.exception("Could not fetch expired ticket channel %s", ticket["channel_id"])
                    database.retry_ticket_deletion(int(ticket["id"]), now + 60)
                    continue

            channel_name = getattr(channel, "name", f"ticket-{ticket['id']}")
            if channel is not None:
                try:
                    await channel.delete(reason="Ticket was not reopened within five minutes")
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    log.exception("Could not delete expired ticket channel %s", channel.id)
                    database.retry_ticket_deletion(int(ticket["id"]), now + 60)
                    continue

            database.delete_ticket(int(ticket["id"]))
            await _send_log(
                guild,
                database.get_ticket_config(guild.id),
                title=f"Ticket #{ticket['id']} deleted",
                description=(
                    f"Channel: #{channel_name} (`{ticket.get('channel_id') or 'unknown'}`)\n"
                    f"Creator: <@{ticket['user_id']}> (`{ticket['user_id']}`)\n"
                    f"Category: {ticket['category_name']}\n"
                    f"Close reason: {ticket.get('close_reason') or 'Unknown'}\n"
                    "Deleted automatically after the five-minute reopen window."
                ),
                color=0x68738A,
            )

    @cleanup_closed_tickets.before_loop
    async def before_cleanup_closed_tickets(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="panel", description="Send or refresh the configured support ticket panel")
    @app_commands.guild_only()
    @app_commands.check(_can_manage_panel)
    async def refresh_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("Run this command in a server.", ephemeral=True)
        ticket_config = database.get_ticket_config(guild.id)
        if not ticket_config.get("panel_channel_id"):
            return await interaction.followup.send(
                "Set a panel channel in the management page first.", ephemeral=True
            )
        if not ticket_config.get("categories"):
            return await interaction.followup.send(
                "Add at least one ticket category in the management page first.", ephemeral=True
            )
        self._synced_panel_state.pop(guild.id, None)
        synced = await self._sync_guild_panel(guild, ticket_config)
        if not synced:
            return await interaction.followup.send(
                "I couldn't send the panel. Check that the configured channel exists and I can view and send messages there.",
                ephemeral=True,
            )
        await interaction.followup.send(
            f"Ticket panel sent or refreshed in <#{ticket_config['panel_channel_id']}>.",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            message = "Only the server owner, a server administrator, or a configured admin can use `/panel`."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        log.exception("Ticket application command failed", exc_info=error)

    async def cog_load(self):
        # This global persistent view handles close buttons on existing tickets
        # after a bot restart. The category panel views are registered per message.
        self.bot.add_view(TicketControlsView(self.bot))


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
