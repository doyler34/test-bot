# test-bot

A minimal [discord.py](https://discordpy.readthedocs.io/) starter bot.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # then add your bot token
```

## Run

```bash
python main.py
```

## What's here

- `main.py` — bot entry point with an example `!ping` command.
- `.env.example` — template for your `DISCORD_BOT_TOKEN`.

Add commands by defining more `@bot.command()` functions in `main.py`, or split
them into [cogs](https://discordpy.readthedocs.io/en/stable/ext/commands/cogs.html)
as the bot grows.
