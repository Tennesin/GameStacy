"""Единый показ технической ошибки: traceback в консоль, извинение в Discord."""

import logging
from typing import Optional

import discord

from config import DEV_ID

log = logging.getLogger("stacy.errors")

TECH_ERROR_TEXT = ("ПРОИЗОШЛА ТЕХНИЧЕСКАЯ ОШИБКА! ПРОСИМ ИЗВИНЕНИЙ "
                   "И ДОЛОЖИТЬ ПРОИЗОШЕДШЕЕ РАЗРАБОТЧИКУ <@{dev}>")

async def report_error(interaction: discord.Interaction, error: BaseException,
                       closing_text: Optional[str] = None):
    """closing_text: если задан, сообщение с кнопками заменяется этим текстом (сессия закрывается),
    а извинение уходит отдельным сообщением, чтобы упоминание разработчика сработало."""
    log.error("Необработанная ошибка", exc_info=error)      # исходный Traceback в консоль
    text = TECH_ERROR_TEXT.format(dev=DEV_ID)
    try:
        if closing_text is not None:
            if interaction.response.is_done():
                await interaction.edit_original_response(content=closing_text, view=None)
            else:
                await interaction.response.edit_message(content=closing_text, view=None)
            await interaction.followup.send(text)
        elif interaction.response.is_done():
            await interaction.followup.send(text)
        else:
            await interaction.response.send_message(text)
    except discord.HTTPException:
        log.exception("Не удалось показать сообщение об ошибке в Discord")