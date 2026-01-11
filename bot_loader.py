"""
Bot loader module for automatically loading child bot configurations.

This module scans the config/bots/ directory and loads all bot configurations,
registering their capabilities and handlers.
"""

import os
import json
import logging
from typing import Dict, List, Optional
from pathlib import Path
from registry import CapabilityRegistry
from executor import CommandExecutor
from utils.bot_handler import create_handler


logger = logging.getLogger(__name__)


def load_bots_from_directory(registry: CapabilityRegistry, executor: CommandExecutor,
                            bots_dir: str = "config/bots") -> Dict[str, int]:
    """Load all bot configurations from a directory.
    
    Args:
        registry: CapabilityRegistry instance
        executor: CommandExecutor instance
        bots_dir: Directory containing bot configuration files
    
    Returns:
        Dictionary with load statistics:
            - bots_loaded: Number of bots loaded
            - capabilities_loaded: Total number of capabilities loaded
            - errors: Number of errors encountered
    """
    stats = {
        'bots_loaded': 0,
        'capabilities_loaded': 0,
        'errors': 0
    }
    
    bots_path = Path(bots_dir)
    
    # Create directory if it doesn't exist
    bots_path.mkdir(parents=True, exist_ok=True)
    
    if not bots_path.exists():
        logger.warning(f"Bots directory does not exist: {bots_dir}")
        return stats
    
    # Find all JSON files in the bots directory
    bot_files = list(bots_path.glob("*.json"))
    
    if not bot_files:
        logger.info(f"No bot configuration files found in {bots_dir}")
        return stats
    
    logger.info(f"Loading bots from {bots_dir}...")
    
    for bot_file in bot_files:
        try:
            bot_config = load_bot_config(bot_file, registry, executor)
            if bot_config:
                stats['bots_loaded'] += 1
                stats['capabilities_loaded'] += bot_config.get('capabilities_count', 0)
            else:
                stats['errors'] += 1
        except Exception as e:
            logger.exception(f"Error loading bot from {bot_file.name}: {e}")
            stats['errors'] += 1
    
    logger.info(
        f"Loaded {stats['bots_loaded']} bots with {stats['capabilities_loaded']} total capabilities"
    )
    
    return stats


def load_bot_config(config_file: Path, registry: CapabilityRegistry,
                   executor: CommandExecutor) -> Optional[Dict]:
    """Load a single bot configuration file.
    
    Supports two formats:
    
    1. Simple format (auto-discovery):
    {
        "bot_name": "my_bot",
        "base_url": "http://localhost:8000/api",
        "api_key": "optional_key_here"
    }
    
    2. Full format (manual capabilities):
    {
        "bot_name": "example_bot",
        "handler": {
            "type": "mock",
            "config": {}
        },
        "capabilities": [...]
    }
    
    Args:
        config_file: Path to the bot configuration JSON file
        registry: CapabilityRegistry instance
        executor: CommandExecutor instance
    
    Returns:
        Dictionary with load information, or None if failed
    """
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        bot_name = config.get('bot_name')
        if not bot_name:
            logger.error(f"Missing 'bot_name' in {config_file.name}")
            return None
        
        capabilities_count = 0
        handler_type = 'mock'  # Default handler type
        
        # Determine handler type and config
        # Check if simple format (has base_url directly)
        if 'base_url' in config:
            # Simple format - auto-discovery
            handler_type = 'http'
            handler_config_data = {
                'base_url': config.get('base_url'),
                'api_key': config.get('api_key'),
                'timeout': config.get('timeout', 10)
            }
            
            # Create handler first to enable discovery
            handler = create_handler(bot_name, handler_type, handler_config_data)
            
            # Try auto-discovery
            if hasattr(handler, 'discover_capabilities'):
                discovered_caps = handler.discover_capabilities()
                if discovered_caps:
                    capabilities_count = registry.register_capabilities_from_api(
                        bot_name, discovered_caps
                    )
                    logger.info(f"Auto-discovered {capabilities_count} capabilities for {bot_name}")
                else:
                    logger.warning(
                        f"Auto-discovery failed for {bot_name}. "
                        "Bot may not support /capabilities endpoint."
                    )
            
            # Fall back to manual capabilities if provided
            manual_capabilities = config.get('capabilities', [])
            if manual_capabilities and capabilities_count == 0:
                logger.info(f"Falling back to manual capabilities for {bot_name}")
                for cap in manual_capabilities:
                    capability_id = cap.get('capability_id')
                    description = cap.get('description', '')
                    parameters = cap.get('parameters', {})
                    permissions_required = cap.get('permissions_required', [])
                    
                    if not capability_id:
                        logger.warning(f"Skipping capability in {bot_name}: missing capability_id")
                        continue
                    
                    if registry.register_capability(
                        bot_name=bot_name,
                        capability_id=capability_id,
                        description=description,
                        parameters=parameters,
                        permissions_required=permissions_required
                    ):
                        capabilities_count += 1
        
        else:
            # Full format - manual configuration
            handler_config = config.get('handler', {})
            handler_type = handler_config.get('type', 'mock')
            handler_config_data = handler_config.get('config', {})
            
            handler = create_handler(bot_name, handler_type, handler_config_data)
            
            # Load manual capabilities
            manual_capabilities = config.get('capabilities', [])
            for cap in manual_capabilities:
                capability_id = cap.get('capability_id')
                description = cap.get('description', '')
                parameters = cap.get('parameters', {})
                permissions_required = cap.get('permissions_required', [])
                
                if not capability_id:
                    logger.warning(f"Skipping capability in {bot_name}: missing capability_id")
                    continue
                
                if registry.register_capability(
                    bot_name=bot_name,
                    capability_id=capability_id,
                    description=description,
                    parameters=parameters,
                    permissions_required=permissions_required
                ):
                    capabilities_count += 1
        
        # Register handler
        executor.register_bot_handler(bot_name, handler)
        
        if capabilities_count == 0:
            logger.warning(
                f"Bot '{bot_name}' loaded with 0 capabilities. "
                "No capabilities discovered or defined in config."
            )
        else:
            logger.info(
                f"Loaded bot '{bot_name}' with {capabilities_count} capabilities "
                f"(handler: {handler_type})"
            )
        
        return {
            'bot_name': bot_name,
            'capabilities_count': capabilities_count,
            'handler_type': handler_type
        }
    
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {config_file.name}: {e}")
        return None
    except Exception as e:
        logger.exception(f"Error loading bot config from {config_file.name}: {e}")
        return None

