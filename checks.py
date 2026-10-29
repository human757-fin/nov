# checks.py
from discord.ext import commands

import config


def is_bot_admin():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        if ctx.author.id == ctx.guild.owner_id:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        return config.ADMIN_ROLE_ID != 0 and any(
            role.id == config.ADMIN_ROLE_ID for role in ctx.author.roles
        )

    return commands.check(predicate)
