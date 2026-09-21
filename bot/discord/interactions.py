"""Answering a Discord click inside the three seconds it allows.

Discord gives a button three seconds to be acknowledged. Anything slower - a
role change, a database read while the bot is busy - and the member gets
"didn't respond in time" even though the work went through. Acknowledging
first buys unlimited time, so every handler that does work before replying
should call ack() on its way in.

The one exception is a modal: Discord will not open a popup form after a
deferral, so those handlers must reply instantly and never call ack().
"""


async def ack(interaction, *, thinking=False):
    """Take the click now. Safe to call twice."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=thinking)


async def say(interaction, text, **kwargs):
    """Send the member a private reply, acknowledged or not."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, **kwargs)
    else:
        await interaction.response.send_message(text, ephemeral=True, **kwargs)


async def update(interaction, **kwargs):
    """Edit the message the button sits on, acknowledged or not."""
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    else:
        await interaction.response.edit_message(**kwargs)
