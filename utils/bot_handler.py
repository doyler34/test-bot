"""
Base bot handler classes for child bots.

This module provides base classes and common handler implementations
for connecting to child bots.
"""

import logging
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod


logger = logging.getLogger(__name__)


class BotHandler(ABC):
    """Abstract base class for bot handlers."""
    
    def __init__(self, bot_name: str, config: Optional[Dict] = None):
        """Initialize the bot handler.
        
        Args:
            bot_name: Name of the bot
            config: Optional configuration dictionary
        """
        self.bot_name = bot_name
        self.config = config or {}
    
    @abstractmethod
    def execute_capability(self, capability_id: str, parameters: Dict,
                          context: Optional[Dict] = None) -> Dict:
        """Execute a capability for this bot.
        
        Args:
            capability_id: ID of the capability to execute
            parameters: Parameters for the capability
            context: Optional execution context (channel_id, guild_id, etc.)
        
        Returns:
            Dictionary with execution result (should include 'status' key)
        """
        pass


class MockBotHandler(BotHandler):
    """Mock bot handler for testing and demonstration."""
    
    def execute_capability(self, capability_id: str, parameters: Dict,
                          context: Optional[Dict] = None) -> Dict:
        """Mock execution - logs what would be executed."""
        logger.info(
            f"[MOCK] {self.bot_name}.{capability_id} executed with parameters: {parameters}"
        )
        return {
            'status': 'success',
            'message': f'Mock execution of {capability_id}',
            'parameters': parameters,
            'bot_name': self.bot_name,
            'capability_id': capability_id
        }


class HTTPBotHandler(BotHandler):
    """HTTP-based bot handler for bots with REST API."""
    
    def __init__(self, bot_name: str, config: Optional[Dict] = None):
        """Initialize HTTP bot handler.
        
        Config should contain:
            - base_url: Base URL for the bot API
            - api_key: Optional API key for authentication
            - timeout: Optional timeout in seconds (default: 10)
        """
        super().__init__(bot_name, config)
        self.base_url = self.config.get('base_url', '')
        self.api_key = self.config.get('api_key')
        self.timeout = self.config.get('timeout', 10)
    
    def discover_capabilities(self) -> Optional[List[Dict]]:
        """Discover capabilities from bot's API endpoint.
        
        Calls GET /capabilities endpoint to fetch available capabilities.
        
        Returns:
            List of capability dictionaries, or None if discovery fails
        """
        if not self.base_url:
            logger.warning(f"No base_url configured for {self.bot_name}, cannot discover capabilities")
            return None
        
        try:
            import requests
            
            url = f"{self.base_url.rstrip('/')}/capabilities"
            headers = {'Content-Type': 'application/json'}
            
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            
            data = response.json()
            capabilities = data.get('capabilities', [])
            
            logger.info(f"Discovered {len(capabilities)} capabilities for {self.bot_name}")
            return capabilities
        
        except ImportError:
            logger.error("requests library not installed. Install with: pip install requests")
            return None
        except Exception as e:
            logger.debug(f"Capability discovery failed for {self.bot_name}: {e}")
            return None
    
    def execute_capability(self, capability_id: str, parameters: Dict,
                          context: Optional[Dict] = None) -> Dict:
        """Execute capability via HTTP POST request."""
        try:
            import requests
            
            url = f"{self.base_url.rstrip('/')}/execute/{capability_id}"
            headers = {'Content-Type': 'application/json'}
            
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            
            payload = {
                'parameters': parameters,
                'context': context or {}
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            
            return {
                'status': 'success',
                'result': response.json(),
                'bot_name': self.bot_name,
                'capability_id': capability_id
            }
        
        except ImportError:
            logger.error("requests library not installed. Install with: pip install requests")
            return {
                'status': 'error',
                'error': 'HTTP handler requires requests library',
                'bot_name': self.bot_name,
                'capability_id': capability_id
            }
        except Exception as e:
            logger.exception(f"HTTP request failed for {self.bot_name}.{capability_id}")
            return {
                'status': 'error',
                'error': str(e),
                'bot_name': self.bot_name,
                'capability_id': capability_id
            }


class WebSocketBotHandler(BotHandler):
    """WebSocket-based bot handler (placeholder for future implementation)."""
    
    def execute_capability(self, capability_id: str, parameters: Dict,
                          context: Optional[Dict] = None) -> Dict:
        """Execute capability via WebSocket (not yet implemented)."""
        logger.warning(f"WebSocket handler not yet implemented for {self.bot_name}")
        return {
            'status': 'error',
            'error': 'WebSocket handler not yet implemented',
            'bot_name': self.bot_name,
            'capability_id': capability_id
        }


def create_handler(bot_name: str, handler_type: str, config: Optional[Dict] = None) -> BotHandler:
    """Factory function to create a bot handler.
    
    Args:
        bot_name: Name of the bot
        handler_type: Type of handler ('mock', 'http', 'websocket')
        config: Optional configuration dictionary
    
    Returns:
        BotHandler instance
    """
    handler_types = {
        'mock': MockBotHandler,
        'http': HTTPBotHandler,
        'websocket': WebSocketBotHandler
    }
    
    handler_class = handler_types.get(handler_type.lower())
    if not handler_class:
        logger.warning(f"Unknown handler type '{handler_type}', using MockBotHandler")
        handler_class = MockBotHandler
    
    return handler_class(bot_name, config)
