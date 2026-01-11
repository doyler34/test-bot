"""
Main entry point for the Grandfather Discord Bot.

This script initializes all components and starts the Discord bot.
"""

import os
import logging
import asyncio
from dotenv import load_dotenv
from database import Database
from registry import CapabilityRegistry
from ai import AICommandGenerator
from executor import CommandExecutor
from discord_integration import GrandfatherBot
from bot_loader import load_bots_from_directory


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def create_directories():
    """Create necessary directories if they don't exist."""
    os.makedirs('logs', exist_ok=True)
    os.makedirs('config', exist_ok=True)




async def main():
    """Main function to initialize and run the bot."""
    # Load environment variables
    load_dotenv()
    
    # Create directories
    create_directories()
    
    # Get Discord bot token
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        logger.error("DISCORD_BOT_TOKEN not found in environment variables!")
        logger.error("Please create a .env file with DISCORD_BOT_TOKEN=your_token")
        return
    
    # Initialize components
    logger.info("Initializing database...")
    database = Database()
    
    logger.info("Initializing capability registry...")
    registry = CapabilityRegistry(database)
    
    logger.info("Initializing command executor...")
    executor = CommandExecutor(database, registry)
    
    # Load all bots from config/bots/ directory
    logger.info("Loading child bots from config/bots/...")
    load_stats = load_bots_from_directory(registry, executor, bots_dir="config/bots")
    
    if load_stats['bots_loaded'] == 0:
        logger.warning(
            "No bots loaded! Add bot configuration files to config/bots/ directory. "
            "See config/bots/example_http_bot.json.example for format."
        )
    
    logger.info("Initializing AI command generator...")
    ai_generator = AICommandGenerator(registry, api_key=os.getenv('GEMINI_API_KEY'))
    
    # Initialize Discord bot
    logger.info("Initializing Discord bot...")
    bot = GrandfatherBot(database, registry, ai_generator, executor)
    
    # Run the bot
    try:
        await bot.start(token)
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested")
    except Exception as e:
        logger.exception(f"Bot error: {e}")
    finally:
        await bot.close()
        logger.info("Bot disconnected")


if __name__ == '__main__':
    asyncio.run(main())
