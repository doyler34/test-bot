"""
Capability registry module for managing bot capabilities.

This module handles loading, updating, and querying capabilities
from all registered child bots.
"""

import json
import os
import logging
from typing import List, Dict, Optional
from database import Database


logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """Manages the registry of all bot capabilities."""
    
    def __init__(self, database: Database):
        """Initialize the registry with a database connection.
        
        Args:
            database: Database instance for storing capabilities
        """
        self.db = database
        self.capabilities_cache: Dict[str, Dict] = {}
        self._load_capabilities_from_db()
    
    def _load_capabilities_from_db(self):
        """Load all capabilities from the database into cache."""
        capabilities = self.db.get_all_capabilities()
        for cap in capabilities:
            key = f"{cap['bot_name']}:{cap['capability_id']}"
            self.capabilities_cache[key] = cap
    
    def register_capability(self, bot_name: str, capability_id: str, description: str,
                           parameters: Optional[Dict] = None,
                           permissions_required: Optional[List[str]] = None) -> bool:
        """Register a new capability.
        
        Returns:
            True if registered successfully, False if already exists
        """
        success = self.db.register_capability(
            bot_name, capability_id, description, parameters, permissions_required
        )
        if success:
            # Update cache
            key = f"{bot_name}:{capability_id}"
            self.capabilities_cache[key] = {
                'bot_name': bot_name,
                'capability_id': capability_id,
                'description': description,
                'parameters': parameters or {},
                'permissions_required': permissions_required or []
            }
            # Log the registration
            self.db.log_action(
                'capability_registered',
                details={
                    'bot_name': bot_name,
                    'capability_id': capability_id,
                    'description': description
                }
            )
        return success
    
    def get_capability(self, bot_name: str, capability_id: str) -> Optional[Dict]:
        """Get a specific capability."""
        key = f"{bot_name}:{capability_id}"
        if key in self.capabilities_cache:
            return self.capabilities_cache[key]
        # Try loading from database if not in cache
        cap = self.db.get_capability(bot_name, capability_id)
        if cap:
            self.capabilities_cache[key] = cap
        return cap
    
    def get_all_capabilities(self, bot_name: Optional[str] = None) -> List[Dict]:
        """Get all capabilities, optionally filtered by bot_name.
        
        Returns:
            List of capability dictionaries
        """
        if bot_name:
            return [
                cap for key, cap in self.capabilities_cache.items()
                if cap['bot_name'] == bot_name
            ]
        return list(self.capabilities_cache.values())
    
    def search_capabilities(self, query: str) -> List[Dict]:
        """Search capabilities by description.
        
        Args:
            query: Search query (case-insensitive)
        
        Returns:
            List of matching capabilities
        """
        query_lower = query.lower()
        results = []
        for cap in self.capabilities_cache.values():
            if (query_lower in cap['description'].lower() or
                query_lower in cap['capability_id'].lower() or
                query_lower in cap['bot_name'].lower()):
                results.append(cap)
        return results
    
    def validate_command_steps(self, steps: List[Dict]) -> tuple[bool, Optional[str]]:
        """Validate that all steps reference existing capabilities.
        
        Args:
            steps: List of command steps with bot_name and capability_id
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        for i, step in enumerate(steps):
            bot_name = step.get('bot_name')
            capability_id = step.get('capability_id')
            
            if not bot_name or not capability_id:
                return False, f"Step {i+1} missing bot_name or capability_id"
            
            if not self.get_capability(bot_name, capability_id):
                return False, f"Step {i+1}: Capability '{capability_id}' not found for bot '{bot_name}'"
        
        return True, None
    
    def register_capabilities_from_api(self, bot_name: str, capabilities: List[Dict]) -> int:
        """Register multiple capabilities from API response.
        
        Args:
            bot_name: Name of the bot
            capabilities: List of capability dictionaries from API
        
        Returns:
            Number of capabilities successfully registered
        """
        count = 0
        for cap in capabilities:
            capability_id = cap.get('capability_id')
            description = cap.get('description', '')
            parameters = cap.get('parameters', {})
            permissions_required = cap.get('permissions_required', [])
            
            if not capability_id:
                logger.warning(f"Skipping capability in {bot_name}: missing capability_id")
                continue
            
            if self.register_capability(
                bot_name,
                capability_id,
                description,
                parameters,
                permissions_required
            ):
                count += 1
        
        return count
    
    def load_capabilities_from_file(self, file_path: str):
        """Load capabilities from a JSON file (for child bot registration).
        
        Expected format:
        {
            "bot_name": "example_bot",
            "capabilities": [
                {
                    "capability_id": "send_message",
                    "description": "Sends a message to a channel",
                    "parameters": {"channel_id": "string", "message": "string"},
                    "permissions_required": ["execute"]
                }
            ]
        }
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Capability file not found: {file_path}")
        
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        bot_name = data.get('bot_name')
        if not bot_name:
            raise ValueError("Missing 'bot_name' in capability file")
        
        capabilities = data.get('capabilities', [])
        for cap in capabilities:
            self.register_capability(
                bot_name,
                cap['capability_id'],
                cap['description'],
                cap.get('parameters', {}),
                cap.get('permissions_required', [])
            )

