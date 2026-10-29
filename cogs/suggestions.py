import discord
from discord import app_commands
from discord.ext import commands

import config
import database

SUGGESTIONS_PER_PAGE = 8
DISPLAY_TEXT_LIMIT = 380
OWN_SUGGESTIONS_PER_PAGE = 8
OWN_DISPLAY_TEXT_LIMIT = 220
DISPLAY_NOTE_LIMIT = 150
STATUS_LABELS = {
    "pending": "Pending review",
    "approved": "Approved",
    "denied": "Denied",
    "implemented": "Implemented",
}
BUILT_IN_FEATURES = [
    (
        "Built-in · Moderation",
        "`/ban`, `/kick`, and `/unban` help authorized moderators manage server membership.",
    ),
    (
        "Built-in · Timeouts",
        "`/timeout` and `/untimeout` let authorized moderators apply or remove member timeouts.",
    ),
    (
        "Built-in · Welcome embeds",
        "The bot posts a welcome embed when someone joins. Server admins can set its channel, text, title, and color with `/welcome`.",
    ),
    (
        "Built-in · Bot latency",
        "Use `/ping` or `!ping` to check the bot's response latency.",
    ),
    (
        "Built-in · Feature suggestions",
        "Members can submit ideas with `/suggest` and check their review status with `/mysuggestions`.",
    ),
]


def can_review_suggestions():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        if interaction.user.id == interaction.guild.owner_id:
            return True
        if interaction.user.guild_permissions.administrator:
            return True
        return config.ADMIN_ROLE_ID != 0 and any(
            role.id == config.ADMIN_ROLE_ID for role in interaction.user.roles
        )

    return app_commands.check(predicate)


class Suggestions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="suggest", description="Suggest a feature for the bot")
    @app_commands.guild_only()
    @app_commands.describe(
        idea="Describe the feature you'd like to see (up to 1000 characters)"
    )
    async def suggest(self, interaction: discord.Interaction, idea: str):
        idea = idea.strip()
        if not idea:
            return await interaction.response.send_message(
                "Please include a feature idea.", ephemeral=True
            )
        if len(idea) > 1000:
            return await interaction.response.send_message(
                "Please keep suggestions to 1000 characters or fewer.", ephemeral=True
            )

        suggestion_id = database.add_suggestion(
            interaction.guild_id,
            interaction.user.id,
            interaction.user.display_name,
            idea,
        )
        await interaction.response.send_message(
            f"Thanks! Your feature suggestion was saved as `#{suggestion_id}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="suggestions", description="Privately review submitted feature suggestions"
    )
    @app_commands.guild_only()
    @app_commands.describe(page="Page number, with the newest suggestions first")
    @can_review_suggestions()
    async def list_suggestions(
        self,
        interaction: discord.Interaction,
        page: app_commands.Range[int, 1, 10000] = 1,
    ):
        guild_id = interaction.guild_id
        total = database.count_suggestions(guild_id)
        page_count = max(1, (total + SUGGESTIONS_PER_PAGE - 1) // SUGGESTIONS_PER_PAGE)
        if page > page_count:
            return await interaction.response.send_message(
                f"There are {total} suggestions across {page_count} page(s).",
                ephemeral=True,
            )

        suggestions = database.get_suggestions(
            guild_id,
            SUGGESTIONS_PER_PAGE,
            (page - 1) * SUGGESTIONS_PER_PAGE,
        )
        embed = discord.Embed(
            title=f"Feature suggestions · page {page}/{page_count}",
            color=0xFF9ECF,
        )
        if not suggestions:
            embed.description = "No feature suggestions yet."
        else:
            entries = []
            for item in suggestions:
                submitter = discord.utils.escape_markdown(item["submitter"])
                text = item["suggestion"]
                if len(text) > DISPLAY_TEXT_LIMIT:
                    text = text[:DISPLAY_TEXT_LIMIT].rstrip() + "…"
                entries.append(
                    f"**#{item['id']} · {submitter} · {STATUS_LABELS[item['status']]}** "
                    f"· {item['created_at']}\n{text}"
                )
            embed.description = "\n\n".join(entries)

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="features", description="See approved and implemented bot features"
    )
    @app_commands.guild_only()
    @app_commands.describe(page="Page number")
    async def list_features(
        self,
        interaction: discord.Interaction,
        page: app_commands.Range[int, 1, 10000] = 1,
    ):
        guild_id = interaction.guild_id
        total = len(BUILT_IN_FEATURES) + database.count_public_features(guild_id)
        page_count = max(1, (total + SUGGESTIONS_PER_PAGE - 1) // SUGGESTIONS_PER_PAGE)
        if page > page_count:
            return await interaction.response.send_message(
                f"There are {total} features across {page_count} page(s).",
                ephemeral=True,
            )

        offset = (page - 1) * SUGGESTIONS_PER_PAGE
        page_items = BUILT_IN_FEATURES[offset : offset + SUGGESTIONS_PER_PAGE]
        if offset + SUGGESTIONS_PER_PAGE > len(BUILT_IN_FEATURES):
            dynamic_offset = max(0, offset - len(BUILT_IN_FEATURES))
            dynamic_limit = SUGGESTIONS_PER_PAGE - len(page_items)
            features = database.get_public_features(
                guild_id, dynamic_limit, dynamic_offset
            )
            for feature in features:
                page_items.append(
                    (
                        STATUS_LABELS[feature["status"]],
                        feature["suggestion"],
                    )
                )

        embed = discord.Embed(
            title=f"Bot features · page {page}/{page_count}",
            color=0xFF9ECF,
        )
        if not page_items:
            embed.description = "No features are listed yet."
        else:
            entries = []
            for status, feature_text in page_items:
                text = feature_text
                if len(text) > DISPLAY_TEXT_LIMIT:
                    text = text[:DISPLAY_TEXT_LIMIT].rstrip() + "…"
                entries.append(f"**{status}** · {text}")
            embed.description = "\n\n".join(entries)

        await interaction.response.send_message(
            embed=embed,
            ephemeral=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="mysuggestions", description="Check the status of your feature suggestions"
    )
    @app_commands.guild_only()
    @app_commands.describe(page="Page number, with your newest suggestions first")
    async def my_suggestions(
        self,
        interaction: discord.Interaction,
        page: app_commands.Range[int, 1, 10000] = 1,
    ):
        guild_id = interaction.guild_id
        total = database.count_user_suggestions(guild_id, interaction.user.id)
        page_count = max(
            1, (total + OWN_SUGGESTIONS_PER_PAGE - 1) // OWN_SUGGESTIONS_PER_PAGE
        )
        if page > page_count:
            return await interaction.response.send_message(
                f"You have {total} suggestion(s) across {page_count} page(s).",
                ephemeral=True,
            )

        suggestions = database.get_user_suggestions(
            guild_id,
            interaction.user.id,
            OWN_SUGGESTIONS_PER_PAGE,
            (page - 1) * OWN_SUGGESTIONS_PER_PAGE,
        )
        embed = discord.Embed(
            title=f"Your suggestions · page {page}/{page_count}",
            color=0xFF9ECF,
        )
        if not suggestions:
            embed.description = "You haven't submitted any feature suggestions yet."
        else:
            entries = []
            for item in suggestions:
                text = item["suggestion"]
                if len(text) > OWN_DISPLAY_TEXT_LIMIT:
                    text = text[:OWN_DISPLAY_TEXT_LIMIT].rstrip() + "…"
                entry = (
                    f"**#{item['id']} · {STATUS_LABELS[item['status']]}** "
                    f"· {item['created_at']}\n{text}"
                )
                if item["status"] == "denied" and item["status_note"]:
                    note = item["status_note"]
                    if len(note) > DISPLAY_NOTE_LIMIT:
                        note = note[:DISPLAY_NOTE_LIMIT].rstrip() + "…"
                    entry += f"\nReviewer note: {note}"
                entries.append(entry)
            embed.description = "\n\n".join(entries)

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="suggestion-status", description="Update a feature suggestion's status"
    )
    @app_commands.guild_only()
    @app_commands.describe(
        suggestion_id="ID shown by /suggestions",
        status="New status",
        note="Optional reviewer note; included in a denial DM",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Pending", value="pending"),
            app_commands.Choice(name="Approved", value="approved"),
            app_commands.Choice(name="Denied", value="denied"),
            app_commands.Choice(name="Implemented", value="implemented"),
        ]
    )
    @can_review_suggestions()
    async def update_status(
        self,
        interaction: discord.Interaction,
        suggestion_id: app_commands.Range[int, 1, 2147483647],
        status: app_commands.Choice[str],
        note: str | None = None,
    ):
        if note and len(note) > 500:
            return await interaction.response.send_message(
                "Reviewer notes must be 500 characters or fewer.", ephemeral=True
            )

        item = database.set_suggestion_status(
            interaction.guild_id,
            suggestion_id,
            status.value,
            note.strip() if note else None,
            interaction.user.id,
        )
        if item is None:
            return await interaction.response.send_message(
                f"Suggestion `#{suggestion_id}` wasn't found in this server.",
                ephemeral=True,
            )

        notification_result = ""
        if status.value == "denied" and item["previous_status"] != "denied":
            try:
                submitter = self.bot.get_user(item["user_id"])
                if submitter is None:
                    submitter = await self.bot.fetch_user(item["user_id"])
                message = (
                    f"Your feature suggestion `#{suggestion_id}` in "
                    f"**{interaction.guild.name}** was denied."
                )
                if item["status_note"]:
                    message += f"\n\nReviewer note: {item['status_note']}"
                await submitter.send(
                    message, allowed_mentions=discord.AllowedMentions.none()
                )
                notification_result = " The submitter was notified by DM."
            except discord.HTTPException:
                notification_result = (
                    " I couldn't DM the submitter; they may have DMs disabled."
                )

        await interaction.response.send_message(
            f"Suggestion `#{suggestion_id}` is now **{STATUS_LABELS[status.value]}**."
            f"{notification_result}",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "Only the server owner, a server administrator, or configured admin role can review suggestions.",
                ephemeral=True,
            )
        else:
            raise error


async def setup(bot):
    await bot.add_cog(Suggestions(bot))
