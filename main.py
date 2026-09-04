"""
A minimal Discord bot starter.

Set DISCORD_BOT_TOKEN in a .env file (see .env.example), then run:

    python main.py
"""

import os
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# discord.py needs the message content intent to read command text.
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info("Logged in as %s (id: %s)", bot.user, bot.user.id)


@bot.command()
async def ping(ctx: commands.Context):
    """Simple health-check command: replies with latency."""
    await ctx.send(f"Pong! {round(bot.latency * 1000)}ms")


def main():
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_BOT_TOKEN not set. Copy .env.example to .env and add your token."
        )
    bot.run(token)


if __name__ == "__main__":
    main()
