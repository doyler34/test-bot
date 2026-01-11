"""
Discord integration module for bot commands and interactions.

This module handles:
- Discord bot setup and connection
- Command definitions (slash commands)
- Permission validation
- User interaction handling
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
import json
import os
from pathlib import Path
from typing import Optional
from database import Database
from registry import CapabilityRegistry
from ai import AICommandGenerator
from executor import CommandExecutor
from bot_loader import load_bot_config


logger = logging.getLogger(__name__)


class GrandfatherBot(commands.Bot):
    """Main Discord bot class for the grandfather bot."""
    
    def __init__(self, database: Database, registry: CapabilityRegistry,
                 ai_generator: AICommandGenerator, executor: CommandExecutor,
                 command_prefix: str = '!', intents: Optional[discord.Intents] = None):
        """Initialize the Discord bot.
        
        Args:
            database: Database instance
            registry: CapabilityRegistry instance
            ai_generator: AICommandGenerator instance
            executor: CommandExecutor instance
            command_prefix: Prefix for text commands (legacy support)
            intents: Discord intents
        """
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True
        
        super().__init__(command_prefix=command_prefix, intents=intents)
        
        self.database = database
        self.registry = registry
        self.ai_generator = ai_generator
        self.executor = executor
        self.tree = app_commands.CommandTree(self)
        
        # Setup commands
        self.setup_commands()
    
    async def setup_hook(self):
        """Called when the bot is starting up."""
        await self.tree.sync()
        logger.info("Discord commands synced")
    
    def setup_commands(self):
        """Register all slash commands."""
        
        @self.tree.command(name="create_custom_command", description="Create a new custom command using AI")
        @app_commands.describe(
            instruction="Natural language instruction for the command",
            command_name="Optional: Custom name for the command (auto-generated if not provided)"
        )
        async def create_custom_command(interaction: discord.Interaction, instruction: str, command_name: Optional[str] = None):
            await interaction.response.defer(thinking=True)
            
            user_id = str(interaction.user.id)
            username = interaction.user.name
            
            # Generate command using AI
            result = self.ai_generator.generate_command_from_natural_language(instruction, user_id)
            
            if not result.get('success'):
                await interaction.followup.send(
                    f"❌ Failed to generate command: {result.get('error', 'Unknown error')}"
                )
                return
            
            # Use provided command_name or generated one
            final_command_name = command_name or result.get('command_name')
            if not final_command_name:
                await interaction.followup.send("❌ Could not determine command name")
                return
            
            # Save command to database
            success = self.database.save_custom_command(
                final_command_name,
                result.get('description', ''),
                result.get('steps', []),
                user_id
            )
            
            if not success:
                await interaction.followup.send(
                    f"❌ Command name '{final_command_name}' already exists. Please choose a different name."
                )
                return
            
            # Log the creation
            self.database.log_action(
                'command_created',
                user_id=user_id,
                username=username,
                details={
                    'command_name': final_command_name,
                    'description': result.get('description'),
                    'steps': result.get('steps')
                },
                success=True
            )
            
            # Format response
            steps_summary = "\n".join([
                f"  {i+1}. {step['bot_name']}.{step['capability_id']}"
                for i, step in enumerate(result.get('steps', []))
            ])
            
            await interaction.followup.send(
                f"✅ Command '{final_command_name}' created successfully!\n\n"
                f"**Description:** {result.get('description', 'N/A')}\n\n"
                f"**Steps:**\n{steps_summary}\n\n"
                f"Use `/execute command:{final_command_name}` to run it."
            )
        
        @self.tree.command(name="list_commands", description="List all available custom commands")
        async def list_commands(interaction: discord.Interaction):
            await interaction.response.defer()
            
            commands = self.database.get_all_commands()
            
            if not commands:
                await interaction.followup.send("📝 No custom commands found.")
                return
            
            # Format command list
            command_list = []
            for cmd in commands:
                steps_count = len(cmd.get('steps', []))
                command_list.append(
                    f"**{cmd['command_name']}** - {cmd['description']} ({steps_count} step{'s' if steps_count != 1 else ''})"
                )
            
            response = "📋 **Available Commands:**\n\n" + "\n".join(command_list)
            
            # Discord has a 2000 character limit
            if len(response) > 2000:
                response = response[:1997] + "..."
            
            await interaction.followup.send(response)
        
        @self.tree.command(name="execute", description="Execute a custom command")
        @app_commands.describe(command_name="Name of the command to execute")
        async def execute(interaction: discord.Interaction, command_name: str):
            await interaction.response.defer()
            
            user_id = str(interaction.user.id)
            username = interaction.user.name
            context = {
                'channel_id': str(interaction.channel.id),
                'guild_id': str(interaction.guild.id) if interaction.guild else None,
                'user_id': user_id
            }
            
            # Execute the command
            result = self.executor.execute_command(command_name, user_id, username, context)
            
            if not result.get('success'):
                await interaction.followup.send(
                    f"❌ Execution failed: {result.get('error', 'Unknown error')}"
                )
                return
            
            # Format success response
            steps_executed = result.get('steps_executed', 0)
            total_steps = result.get('total_steps', 0)
            results = result.get('results', [])
            
            response_parts = [
                f"✅ Command '{command_name}' executed successfully!",
                f"**Steps executed:** {steps_executed}/{total_steps}"
            ]
            
            # Add step results summary
            for step_result in results:
                status = "✅" if step_result.get('success') else "❌"
                response_parts.append(
                    f"{status} Step {step_result['step']}: {step_result['bot_name']}.{step_result['capability_id']}"
                )
            
            response = "\n".join(response_parts)
            
            if len(response) > 2000:
                response = response[:1997] + "..."
            
            await interaction.followup.send(response)
        
        @self.tree.command(name="list_capabilities", description="List all registered bot capabilities")
        @app_commands.describe(bot_name="Optional: Filter by bot name")
        async def list_capabilities(interaction: discord.Interaction, bot_name: Optional[str] = None):
            await interaction.response.defer()
            
            capabilities = self.registry.get_all_capabilities(bot_name)
            
            if not capabilities:
                filter_msg = f" for bot '{bot_name}'" if bot_name else ""
                await interaction.followup.send(f"📝 No capabilities found{filter_msg}.")
                return
            
            # Group by bot
            bots = {}
            for cap in capabilities:
                bot = cap['bot_name']
                if bot not in bots:
                    bots[bot] = []
                bots[bot].append(cap)
            
            # Format response
            response_parts = ["📋 **Registered Capabilities:**\n"]
            for bot_name, caps in bots.items():
                response_parts.append(f"**{bot_name}:**")
                for cap in caps:
                    response_parts.append(f"  - `{cap['capability_id']}`: {cap['description']}")
                response_parts.append("")
            
            response = "\n".join(response_parts)
            
            if len(response) > 2000:
                response = response[:1997] + "..."
            
            await interaction.followup.send(response)
        
        @self.tree.command(name="command_info", description="Get detailed information about a command")
        @app_commands.describe(command_name="Name of the command")
        async def command_info(interaction: discord.Interaction, command_name: str):
            await interaction.response.defer()
            
            command = self.database.get_custom_command(command_name)
            
            if not command:
                await interaction.followup.send(f"❌ Command '{command_name}' not found.")
                return
            
            # Format detailed info
            steps_info = []
            for i, step in enumerate(command['steps']):
                steps_info.append(
                    f"{i+1}. **{step['bot_name']}.{step['capability_id']}**\n"
                    f"   Parameters: {step.get('parameters', {})}"
                )
            
            response = (
                f"📄 **Command: {command['command_name']}**\n\n"
                f"**Description:** {command['description']}\n\n"
                f"**Steps:**\n" + "\n\n".join(steps_info) + "\n\n"
                f"**Created by:** <@{command['created_by']}>\n"
                f"**Created at:** {command['created_at']}"
            )
            
            if len(response) > 2000:
                response = response[:1997] + "..."
            
            await interaction.followup.send(response)
        
        @self.tree.command(name="add_bot", description="Add a new child bot to the system")
        @app_commands.describe(
            bot_name="Name of the bot (must be unique)",
            base_url="Base URL for the bot's API (e.g., http://localhost:8000/api)",
            api_key="Optional: API key for authentication",
            timeout="Optional: Request timeout in seconds (default: 10)"
        )
        async def add_bot(interaction: discord.Interaction, bot_name: str, base_url: str,
                          api_key: Optional[str] = None, timeout: Optional[int] = 10):
            await interaction.response.defer()
            
            user_id = str(interaction.user.id)
            username = interaction.user.name
            
            # Validate inputs
            if not bot_name or not bot_name.strip():
                await interaction.followup.send("❌ Bot name cannot be empty")
                return
            
            if not base_url or not base_url.strip():
                await interaction.followup.send("❌ Base URL cannot be empty")
                return
            
            # Clean inputs
            bot_name = bot_name.strip().lower()
            base_url = base_url.strip()
            
            # Validate URL format (basic check)
            if not (base_url.startswith('http://') or base_url.startswith('https://')):
                await interaction.followup.send(
                    "❌ Base URL must start with http:// or https://"
                )
                return
            
            # Check if bot config already exists
            bots_dir = Path("config/bots")
            bots_dir.mkdir(parents=True, exist_ok=True)
            config_file = bots_dir / f"{bot_name}.json"
            
            if config_file.exists():
                await interaction.followup.send(
                    f"❌ Bot '{bot_name}' already exists. Use a different name or delete the existing config first."
                )
                return
            
            # Create config dictionary
            config = {
                "bot_name": bot_name,
                "base_url": base_url
            }
            
            if api_key:
                config["api_key"] = api_key.strip()
            
            if timeout and timeout > 0:
                config["timeout"] = timeout
            
            # Save config file
            try:
                with open(config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=4)
                
                # Try to load the bot immediately
                try:
                    load_result = load_bot_config(config_file, self.registry, self.executor)
                    if load_result:
                        capabilities_count = load_result.get('capabilities_count', 0)
                        
                        # Log the action
                        self.database.log_action(
                            'bot_added',
                            user_id=user_id,
                            username=username,
                            details={
                                'bot_name': bot_name,
                                'base_url': base_url,
                                'capabilities_discovered': capabilities_count
                            },
                            success=True
                        )
                        
                        if capabilities_count > 0:
                            await interaction.followup.send(
                                f"✅ Bot '{bot_name}' added successfully!\n\n"
                                f"**Base URL:** {base_url}\n"
                                f"**Capabilities discovered:** {capabilities_count}\n\n"
                                f"The bot is now available for use."
                            )
                        else:
                            await interaction.followup.send(
                                f"✅ Bot '{bot_name}' added successfully!\n\n"
                                f"**Base URL:** {base_url}\n"
                                f"**Capabilities discovered:** 0\n\n"
                                f"⚠️ No capabilities were discovered. Make sure the bot's `/capabilities` endpoint is accessible, "
                                f"or add capabilities manually by editing the config file."
                            )
                    else:
                        # File saved but loading failed
                        await interaction.followup.send(
                            f"✅ Bot '{bot_name}' config file created.\n\n"
                            f"⚠️ Warning: Failed to load the bot. Check logs for details. "
                            f"Restart the grandfather bot for the bot to be available."
                        )
                
                except Exception as e:
                    logger.exception(f"Error loading bot {bot_name} after creation: {e}")
                    await interaction.followup.send(
                        f"✅ Bot '{bot_name}' config file created.\n\n"
                        f"⚠️ Warning: Error loading the bot: {str(e)}\n"
                        f"Restart the grandfather bot for the bot to be available."
                    )
            
            except Exception as e:
                logger.exception(f"Error creating bot config for {bot_name}: {e}")
                self.database.log_action(
                    'bot_added',
                    user_id=user_id,
                    username=username,
                    details={
                        'bot_name': bot_name,
                        'error': str(e)
                    },
                    success=False
                )
                await interaction.followup.send(
                    f"❌ Failed to create bot config: {str(e)}"
                )
    
    async def on_ready(self):
        """Called when the bot is ready."""
        logger.info(f'{self.user} has connected to Discord!')
        logger.info(f'Bot is in {len(self.guilds)} guilds')
