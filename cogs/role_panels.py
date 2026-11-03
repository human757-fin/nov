"""Configurable self-assign roles and CAPTCHA verification panels."""

from __future__ import annotations

import hashlib
import hmac
import io
import logging
import secrets
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont

import config
import database

log = logging.getLogger(__name__)
CAPTCHA_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CAPTCHA_TTL = 5 * 60
CAPTCHA_MAX_ATTEMPTS = 5
SELF_ASSIGN_BLOCKED_PERMISSIONS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "manage_emojis_and_stickers",
    "manage_messages",
    "manage_threads",
    "manage_nicknames",
    "kick_members",
    "ban_members",
    "moderate_members",
    "mention_everyone",
)


def _is_safe_self_assign_role(role: discord.Role) -> bool:
    return role.is_assignable() and not any(
        getattr(role.permissions, permission, False)
        for permission in SELF_ASSIGN_BLOCKED_PERMISSIONS
    )


def _management_allowed(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        return False
    if member.id == guild.owner_id or member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_guild:
        return True
    role_ids = set(database.get_ticket_config(guild.id)["admin_role_ids"])
    if config.ADMIN_ROLE_ID:
        role_ids.add(config.ADMIN_ROLE_ID)
    return any(role.id in role_ids for role in member.roles)


def _captcha_image(answer: str) -> bytes:
    width, height = 300, 104
    image = Image.new("RGB", (width, height), (245, 247, 252))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    for index, character in enumerate(answer):
        x = 13 + index * 46 + secrets.randbelow(5)
        y = 22 + secrets.randbelow(12) - 6
        shade = secrets.randbelow(55)
        draw.text((x, y), character, font=font, fill=(25 + shade, 35 + shade, 62 + shade))
    line_points = []
    for x in range(0, width + 1, 20):
        line_points.append((x, height // 2 + secrets.randbelow(13) - 6))
    draw.line(line_points, fill=(91, 105, 137), width=3)
    for _ in range(42):
        x = secrets.randbelow(width)
        y = secrets.randbelow(height)
        draw.point((x, y), fill=(172, 181, 199))
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _role_panel_embed(settings: dict) -> discord.Embed:
    return discord.Embed(
        title=settings["title"],
        description=settings["description"],
        color=0x91A8FF,
    )


def _verification_panel_embed(settings: dict, role: discord.Role | None) -> discord.Embed:
    description = settings["description"]
    if role is not None:
        description += f"\n\nComplete the CAPTCHA below to receive {role.mention}."
    return discord.Embed(
        title=settings["title"],
        description=description,
        color=0x64D7B0,
    )


class RolePickerSelect(discord.ui.Select):
    def __init__(self, cog: "RolePanels", settings: dict, roles: list[discord.Role]):
        options = [
            discord.SelectOption(label=role.name[:100], value=str(role.id))
            for role in roles
        ]
        max_values = len(options) if settings["allow_multiple"] else 1
        super().__init__(
            placeholder="Choose a role to toggle…",
            min_values=1,
            max_values=max_values,
            options=options,
            custom_id=f"nov:roles:{settings['guild_id']}",
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog._toggle_roles(interaction, self.values)


class RolePickerView(discord.ui.View):
    def __init__(self, cog: "RolePanels", settings: dict, roles: list[discord.Role]):
        super().__init__(timeout=None)
        self.add_item(RolePickerSelect(cog, settings, roles))


class VerificationPanelView(discord.ui.View):
    def __init__(self, cog: "RolePanels", guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        button = discord.ui.Button(
            label="Verify",
            style=discord.ButtonStyle.success,
            custom_id=f"nov:verify:{guild_id}",
        )
        button.callback = self._verify
        self.add_item(button)

    async def _verify(self, interaction: discord.Interaction) -> None:
        await self.cog._start_verification(interaction)


class CaptchaEntryView(discord.ui.View):
    def __init__(self, cog: "RolePanels", guild_id: int, user_id: int, challenge_id: str):
        super().__init__(timeout=CAPTCHA_TTL)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.challenge_id = challenge_id
        button = discord.ui.Button(
            label="Enter CAPTCHA",
            style=discord.ButtonStyle.primary,
            custom_id=f"nov:captcha:{challenge_id}",
        )
        button.callback = self._open_modal
        self.add_item(button)

    async def _open_modal(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id or interaction.guild_id != self.guild_id:
            return await interaction.response.send_message(
                "This CAPTCHA belongs to another member.", ephemeral=True
            )
        challenge = self.cog.challenges.get((self.guild_id, self.user_id))
        if (
            challenge is None
            or challenge["id"] != self.challenge_id
            or challenge["expires_at"] <= time.time()
            or challenge["attempts"] >= CAPTCHA_MAX_ATTEMPTS
        ):
            self.cog.challenges.pop((self.guild_id, self.user_id), None)
            return await interaction.response.send_message(
                "This CAPTCHA expired. Press Verify in the panel to get a new one.",
                ephemeral=True,
            )
        await interaction.response.send_modal(CaptchaModal(self.cog, self.challenge_id))


class CaptchaModal(discord.ui.Modal):
    def __init__(self, cog: "RolePanels", challenge_id: str):
        super().__init__(title="Complete verification", timeout=CAPTCHA_TTL)
        self.cog = cog
        self.challenge_id = challenge_id
        self.answer = discord.ui.TextInput(
            label="Type the six characters shown",
            min_length=6,
            max_length=6,
            required=True,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog._check_captcha(interaction, self.challenge_id, self.answer.value)


class RolePanels(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.challenges: dict[tuple[int, int], dict] = {}
        self.challenge_cooldowns: dict[tuple[int, int], float] = {}
        self._captcha_secret = secrets.token_bytes(32)
        self._synced_roles: dict[int, tuple] = {}
        self._synced_verification: dict[int, tuple] = {}
        self.sync_panels.start()

    async def cog_unload(self) -> None:
        self.sync_panels.cancel()

    @tasks.loop(seconds=60)
    async def sync_panels(self) -> None:
        now = time.time()
        for key, challenge in list(self.challenges.items()):
            if challenge["expires_at"] <= now:
                self.challenges.pop(key, None)
        for key, requested_at in list(self.challenge_cooldowns.items()):
            if now - requested_at > 60:
                self.challenge_cooldowns.pop(key, None)
        for guild in self.bot.guilds:
            await self._sync_role_picker(guild, database.get_role_picker_config(guild.id))
            await self._sync_verification(guild, database.get_verification_config(guild.id))

    @sync_panels.before_loop
    async def before_sync_panels(self) -> None:
        await self.bot.wait_until_ready()

    async def _sync_role_picker(self, guild: discord.Guild, settings: dict) -> bool:
        channel_id = settings.get("panel_channel_id")
        message_id = settings.get("panel_message_id")
        if not channel_id:
            return False
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            log.warning("Role picker channel %s is unavailable in guild %s", channel_id, guild.id)
            return False
        bot_member = guild.me
        roles = []
        if bot_member is not None and bot_member.guild_permissions.manage_roles:
            for role_id in settings["role_ids"][:25]:
                role = guild.get_role(int(role_id))
                if role is not None and _is_safe_self_assign_role(role):
                    roles.append(role)
        state = (
            int(channel_id), message_id, settings["title"], settings["description"],
            bool(settings["allow_multiple"]), tuple((role.id, role.name) for role in roles),
        )
        if self._synced_roles.get(guild.id) == state:
            return bool(roles)
        try:
            message = await self._fetch_panel_message(channel, message_id)
        except discord.HTTPException:
            return False
        try:
            if not roles:
                if message is not None:
                    await message.delete()
                database.set_role_picker_panel_message(guild.id, None)
                self._synced_roles[guild.id] = state
                return False
            view = RolePickerView(self, settings, roles)
            if message is None:
                message = await channel.send(
                    embed=_role_panel_embed(settings),
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                database.set_role_picker_panel_message(guild.id, message.id)
            else:
                await message.edit(embed=_role_panel_embed(settings), view=view)
            self.bot.add_view(RolePickerView(self, settings, roles), message_id=message.id)
            self._synced_roles[guild.id] = (
                int(channel_id), message.id, *state[2:]
            )
            return True
        except discord.HTTPException:
            log.exception("Could not update role picker panel for guild %s", guild.id)
            return False

    async def _sync_verification(self, guild: discord.Guild, settings: dict) -> bool:
        channel_id = settings.get("panel_channel_id")
        message_id = settings.get("panel_message_id")
        role_id = settings.get("verified_role_id")
        if not channel_id:
            return False
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            log.warning("Verification panel channel %s is unavailable in guild %s", channel_id, guild.id)
            return False
        role = guild.get_role(int(role_id)) if role_id else None
        bot_member = guild.me
        active = (
            role is not None
            and _is_safe_self_assign_role(role)
            and bot_member is not None
            and bot_member.guild_permissions.manage_roles
        )
        state = (int(channel_id), message_id, role.id if active else None, settings["title"], settings["description"])
        if self._synced_verification.get(guild.id) == state:
            return bool(active)
        try:
            message = await self._fetch_panel_message(channel, message_id)
        except discord.HTTPException:
            return False
        try:
            if not active:
                if message is not None:
                    await message.delete()
                database.set_verification_panel_message(guild.id, None)
                self._synced_verification[guild.id] = state
                return False
            view = VerificationPanelView(self, guild.id)
            embed = _verification_panel_embed(settings, role)
            if message is None:
                message = await channel.send(
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                database.set_verification_panel_message(guild.id, message.id)
            else:
                await message.edit(embed=embed, view=view)
            self.bot.add_view(VerificationPanelView(self, guild.id), message_id=message.id)
            self._synced_verification[guild.id] = (
                int(channel_id), message.id, role.id, settings["title"], settings["description"]
            )
            return True
        except discord.HTTPException:
            log.exception("Could not update verification panel for guild %s", guild.id)
            return False

    async def _fetch_panel_message(
        self, channel: discord.TextChannel, message_id: int | None
    ) -> discord.Message | None:
        if not message_id:
            return None
        try:
            return await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return None
        except discord.HTTPException:
            log.exception("Could not fetch panel message %s", message_id)
            raise

    async def _toggle_roles(self, interaction: discord.Interaction, values: list[str]) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "This role menu only works in a server.", ephemeral=True
            )
        settings = database.get_role_picker_config(interaction.guild.id)
        allowed_ids = set(settings["role_ids"])
        try:
            selected_ids = {int(value) for value in values}
        except ValueError:
            return await interaction.response.send_message(
                "That role selection is invalid. Please use the current panel.", ephemeral=True
            )
        if not settings["allow_multiple"] and len(values) != 1:
            return await interaction.response.send_message(
                "This panel allows one role at a time. Please use the current panel.", ephemeral=True
            )
        if not selected_ids or not selected_ids.issubset(allowed_ids):
            return await interaction.response.send_message(
                "That role is no longer available. Please use the updated panel.", ephemeral=True
            )
        member = interaction.user
        bot_member = interaction.guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                "I need Manage Roles to update your roles.", ephemeral=True
            )
        configured_roles = {
            role_id: interaction.guild.get_role(role_id) for role_id in allowed_ids
        }
        selected_roles = [configured_roles[role_id] for role_id in selected_ids]
        if any(role is None or not _is_safe_self_assign_role(role) for role in selected_roles):
            return await interaction.response.send_message(
                "One of those roles is unavailable or above my highest role. Ask an admin to update the role panel.",
                ephemeral=True,
            )

        current_role_ids = {role.id for role in member.roles}
        roles_to_add: list[discord.Role] = []
        roles_to_remove: list[discord.Role] = []
        if settings["allow_multiple"]:
            for role in selected_roles:
                if role.id in current_role_ids:
                    roles_to_remove.append(role)
                else:
                    roles_to_add.append(role)
        else:
            selected_role = selected_roles[0]
            already_selected = selected_role.id in current_role_ids
            unmanageable = [
                role for role_id, role in configured_roles.items()
                if role is not None and role_id in current_role_ids and not _is_safe_self_assign_role(role)
            ]
            if unmanageable:
                return await interaction.response.send_message(
                    "I can no longer manage one of your selected roles. Ask an admin to fix the bot's role position.",
                    ephemeral=True,
                )
            roles_to_remove = [
                role for role_id, role in configured_roles.items()
                if role is not None and role_id in current_role_ids
            ]
            if not already_selected:
                roles_to_add.append(selected_role)

        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            reason = f"Self-assign role panel used by {member} ({member.id})"
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason=reason)
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=reason)
        except discord.Forbidden:
            return await interaction.followup.send(
                "I couldn't update your roles. Check my Manage Roles permission and role position.",
                ephemeral=True,
            )
        except discord.HTTPException:
            log.exception("Could not update selected roles for member %s", member.id)
            return await interaction.followup.send(
                "Discord couldn't update your roles. Please try again.", ephemeral=True
            )
        changed = [
            f"+ {discord.utils.escape_markdown(role.name)}" for role in roles_to_add
        ] + [
            f"− {discord.utils.escape_markdown(role.name)}" for role in roles_to_remove
        ]
        if not changed:
            changed = ["No role changes were needed"]
        await interaction.followup.send(
            f"Your role selection was updated: {', '.join(changed)}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _start_verification(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "Verification is only available in a server.", ephemeral=True
            )
        settings = database.get_verification_config(guild.id)
        role_id = settings.get("verified_role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        bot_member = guild.me
        if (
            role is None
            or bot_member is None
            or not bot_member.guild_permissions.manage_roles
            or not _is_safe_self_assign_role(role)
        ):
            return await interaction.response.send_message(
                "Verification is not configured correctly. Ask a server admin to check the verified role and bot role position.",
                ephemeral=True,
            )
        if role in interaction.user.roles:
            return await interaction.response.send_message(
                "You're already verified.", ephemeral=True
            )

        now = time.time()
        key = (guild.id, interaction.user.id)
        for old_key, old_challenge in list(self.challenges.items()):
            if old_challenge["expires_at"] <= now:
                self.challenges.pop(old_key, None)
                self.challenge_cooldowns.pop(old_key, None)
        if now - self.challenge_cooldowns.get(key, 0) < 15:
            return await interaction.response.send_message(
                "Please wait a few seconds before requesting another CAPTCHA.", ephemeral=True
            )
        self.challenge_cooldowns[key] = now

        answer = "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(6))
        challenge_id = secrets.token_urlsafe(18)
        digest = hmac.new(
            self._captcha_secret,
            f"{challenge_id}:{answer}".encode(),
            hashlib.sha256,
        ).digest()
        self.challenges[key] = {
            "id": challenge_id,
            "digest": digest,
            "expires_at": now + CAPTCHA_TTL,
            "attempts": 0,
            "in_progress": False,
        }
        file = discord.File(io.BytesIO(_captcha_image(answer)), filename="verification-captcha.png")
        embed = discord.Embed(
            title="Verification CAPTCHA",
            description="Type the six characters shown in the image. This code expires in five minutes.",
            color=0x91A8FF,
        )
        embed.set_image(url="attachment://verification-captcha.png")
        view = CaptchaEntryView(self, guild.id, interaction.user.id, challenge_id)
        await interaction.response.send_message(
            embed=embed, file=file, view=view, ephemeral=True
        )

    async def _check_captcha(
        self, interaction: discord.Interaction, challenge_id: str, submitted: str
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "Verification is only available in a server.", ephemeral=True
            )
        key = (guild.id, interaction.user.id)
        challenge = self.challenges.get(key)
        if (
            challenge is None
            or challenge["id"] != challenge_id
            or challenge["expires_at"] <= time.time()
            or challenge["attempts"] >= CAPTCHA_MAX_ATTEMPTS
        ):
            self.challenges.pop(key, None)
            return await interaction.response.send_message(
                "That CAPTCHA expired or has no attempts left. Press Verify in the panel to start again.",
                ephemeral=True,
            )
        expected = challenge["digest"]
        supplied = hmac.new(
            self._captcha_secret,
            f"{challenge_id}:{submitted.strip().upper()}".encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, supplied):
            challenge["attempts"] += 1
            remaining = CAPTCHA_MAX_ATTEMPTS - challenge["attempts"]
            if remaining <= 0:
                self.challenges.pop(key, None)
                message = "Incorrect. No attempts remain; press Verify in the panel to get a new CAPTCHA."
            else:
                message = f"That code didn't match. You have {remaining} attempts left. Use Enter CAPTCHA to try again."
            return await interaction.response.send_message(message, ephemeral=True)
        if challenge["in_progress"]:
            return await interaction.response.send_message(
                "Your verification is already being processed.", ephemeral=True
            )
        challenge["in_progress"] = True

        settings = database.get_verification_config(guild.id)
        role_id = settings.get("verified_role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        member = interaction.user
        bot_member = guild.me
        if (
            role is None
            or bot_member is None
            or not bot_member.guild_permissions.manage_roles
            or not _is_safe_self_assign_role(role)
        ):
            challenge["in_progress"] = False
            return await interaction.response.send_message(
                "The verified role is unavailable. Ask a server admin to check the panel configuration.",
                ephemeral=True,
            )
        if role in member.roles:
            self.challenges.pop(key, None)
            return await interaction.response.send_message(
                "You're already verified.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await member.add_roles(role, reason=f"CAPTCHA verification completed by {member} ({member.id})")
        except discord.Forbidden:
            challenge["in_progress"] = False
            return await interaction.followup.send(
                "I couldn't grant the verified role. Check my Manage Roles permission and role position.",
                ephemeral=True,
            )
        except discord.HTTPException:
            challenge["in_progress"] = False
            log.exception("Could not grant verified role in guild %s", guild.id)
            return await interaction.followup.send(
                "Discord couldn't grant the verified role. Please try again.", ephemeral=True
            )
        self.challenges.pop(key, None)
        await interaction.followup.send(
            f"You're verified and now have {role.mention}.", ephemeral=True
        )

    @app_commands.command(name="rolepanel", description="Send or refresh the configured self-assign role panel")
    @app_commands.guild_only()
    @app_commands.check(_management_allowed)
    async def refresh_role_panel(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Run this command in a server.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        self._synced_roles.pop(guild.id, None)
        if not await self._sync_role_picker(guild, database.get_role_picker_config(guild.id)):
            return await interaction.followup.send(
                "Configure the role panel, assignable roles, and bot permissions in the management page first.",
                ephemeral=True,
            )
        await interaction.followup.send("The self-assign role panel is up to date.", ephemeral=True)

    @app_commands.command(name="verificationpanel", description="Send or refresh the configured verification panel")
    @app_commands.guild_only()
    @app_commands.check(_management_allowed)
    async def refresh_verification_panel(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Run this command in a server.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        self._synced_verification.pop(guild.id, None)
        if not await self._sync_verification(guild, database.get_verification_config(guild.id)):
            return await interaction.followup.send(
                "Configure the verification channel and verified role in the management page first.",
                ephemeral=True,
            )
        await interaction.followup.send("The verification panel is up to date.", ephemeral=True)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            message = "Only the server owner, a server administrator, or a configured admin can manage these panels."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        log.exception("Role panel application command failed", exc_info=error)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolePanels(bot))
