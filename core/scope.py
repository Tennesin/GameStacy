"""Зона действия игры: играть можно только в канале-хозяине (--gameid) и его ветках."""

import discord

def in_scope(interaction: discord.Interaction, home_id) -> bool:
    """True, если ограничения нет или нажатие пришло из канала-хозяина либо из его ветки."""
    if not home_id:
        return True
    if interaction.channel_id == home_id:
        return True
    return getattr(interaction.channel, "parent_id", None) == home_id

async def ignore(interaction: discord.Interaction, home_id):
    """Гасит нажатие вне зоны игры скрытым сообщением (чтобы у человека не висела ошибка)."""
    text = f"Эта партия идёт в канале <#{home_id}>. Здесь она недоступна."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass