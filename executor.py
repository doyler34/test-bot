"""
Command executor module for running structured commands.

This module handles:
- Step-by-step command execution
- Cross-bot command orchestration
- Error handling and retries
- Result logging
"""

import json
import logging
from typing import List, Dict, Optional, Any
from database import Database
from registry import CapabilityRegistry


logger = logging.getLogger(__name__)


class CommandExecutor:
    """Executes structured commands across multiple bots."""
    
    def __init__(self, database: Database, registry: CapabilityRegistry):
        """Initialize the command executor.
        
        Args:
            database: Database instance for logging
            registry: CapabilityRegistry instance for validation
        """
        self.db = database
        self.registry = registry
        self.bot_handlers: Dict[str, Any] = {}  # Maps bot_name to handler function/class
    
    def register_bot_handler(self, bot_name: str, handler: Any):
        """Register a handler for a specific bot.
        
        Args:
            bot_name: Name of the bot
            handler: Function or class that can execute capabilities for this bot
                    Should have a method: execute_capability(capability_id, parameters)
        """
        self.bot_handlers[bot_name] = handler
    
    def execute_command(self, command_name: str, user_id: str, username: str,
                       context: Optional[Dict] = None) -> Dict:
        """Execute a custom command.
        
        Args:
            command_name: Name of the command to execute
            user_id: Discord user ID executing the command
            username: Discord username
            context: Optional context dictionary (e.g., channel_id, guild_id)
        
        Returns:
            Dictionary with execution results:
                - success: bool
                - steps_executed: int
                - total_steps: int
                - results: List[Dict] (results from each step)
                - error: str (if failed)
        """
        # Load command from database
        command = self.db.get_custom_command(command_name)
        if not command:
            self.db.log_action(
                'command_executed',
                user_id=user_id,
                username=username,
                details={'command_name': command_name, 'error': 'Command not found'},
                success=False
            )
            return {
                'success': False,
                'error': f'Command "{command_name}" not found'
            }
        
        steps = command['steps']
        results = []
        
        logger.info(f"Executing command '{command_name}' for user {username} ({user_id})")
        
        # Execute each step in order
        for i, step in enumerate(steps):
            step_num = i + 1
            bot_name = step.get('bot_name')
            capability_id = step.get('capability_id')
            parameters = step.get('parameters', {})
            
            # Validate capability exists
            capability = self.registry.get_capability(bot_name, capability_id)
            if not capability:
                error_msg = f"Step {step_num}: Capability '{capability_id}' not found for bot '{bot_name}'"
                logger.error(error_msg)
                self.db.log_action(
                    'command_executed',
                    user_id=user_id,
                    username=username,
                    details={
                        'command_name': command_name,
                        'step': step_num,
                        'error': error_msg
                    },
                    success=False
                )
                return {
                    'success': False,
                    'steps_executed': i,
                    'total_steps': len(steps),
                    'results': results,
                    'error': error_msg
                }
            
            # Check permissions
            permissions_required = capability.get('permissions_required', [])
            if permissions_required:
                # Check if user has required permissions
                has_permission = False
                for perm_level in permissions_required:
                    if self.db.check_permission(user_id, bot_name, capability_id, perm_level):
                        has_permission = True
                        break
                
                if not has_permission:
                    error_msg = f"Step {step_num}: Permission denied for '{capability_id}'"
                    logger.warning(error_msg)
                    self.db.log_action(
                        'command_executed',
                        user_id=user_id,
                        username=username,
                        details={
                            'command_name': command_name,
                            'step': step_num,
                            'error': error_msg
                        },
                        success=False
                    )
                    return {
                        'success': False,
                        'steps_executed': i,
                        'total_steps': len(steps),
                        'results': results,
                        'error': error_msg
                    }
            
            # Execute the step
            try:
                step_result = self._execute_step(bot_name, capability_id, parameters, context)
                results.append({
                    'step': step_num,
                    'bot_name': bot_name,
                    'capability_id': capability_id,
                    'success': step_result.get('success', False),
                    'result': step_result.get('result'),
                    'error': step_result.get('error')
                })
                
                if not step_result.get('success', False):
                    # Step failed, stop execution
                    error_msg = f"Step {step_num} failed: {step_result.get('error', 'Unknown error')}"
                    logger.error(error_msg)
                    self.db.log_action(
                        'command_executed',
                        user_id=user_id,
                        username=username,
                        details={
                            'command_name': command_name,
                            'step': step_num,
                            'error': error_msg
                        },
                        success=False
                    )
                    return {
                        'success': False,
                        'steps_executed': i + 1,
                        'total_steps': len(steps),
                        'results': results,
                        'error': error_msg
                    }
            
            except Exception as e:
                error_msg = f"Step {step_num} exception: {str(e)}"
                logger.exception(error_msg)
                self.db.log_action(
                    'command_executed',
                    user_id=user_id,
                    username=username,
                    details={
                        'command_name': command_name,
                        'step': step_num,
                        'error': error_msg
                    },
                    success=False
                )
                return {
                    'success': False,
                    'steps_executed': i + 1,
                    'total_steps': len(steps),
                    'results': results,
                    'error': error_msg
                }
        
        # All steps completed successfully
        self.db.log_action(
            'command_executed',
            user_id=user_id,
            username=username,
            details={
                'command_name': command_name,
                'steps_executed': len(steps),
                'results': results
            },
            success=True
        )
        
        logger.info(f"Command '{command_name}' executed successfully")
        return {
            'success': True,
            'steps_executed': len(steps),
            'total_steps': len(steps),
            'results': results
        }
    
    def _execute_step(self, bot_name: str, capability_id: str, parameters: Dict,
                     context: Optional[Dict] = None) -> Dict:
        """Execute a single step.
        
        Args:
            bot_name: Name of the bot
            capability_id: Capability ID to execute
            parameters: Parameters for the capability
            context: Optional execution context
        
        Returns:
            Dictionary with:
                - success: bool
                - result: Any (result from the capability)
                - error: str (if failed)
        """
        # Check if handler is registered
        if bot_name not in self.bot_handlers:
            return {
                'success': False,
                'error': f'No handler registered for bot "{bot_name}"'
            }
        
        handler = self.bot_handlers[bot_name]
        
        # Try to call the handler
        try:
            # Handler can be a function or an object with execute_capability method
            if callable(handler) and not hasattr(handler, 'execute_capability'):
                # It's a function
                result = handler(capability_id, parameters, context)
            else:
                # It's an object with execute_capability method
                result = handler.execute_capability(capability_id, parameters, context)
            
            return {
                'success': True,
                'result': result
            }
        
        except Exception as e:
            logger.exception(f"Error executing capability {capability_id} for bot {bot_name}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def execute_direct_step(self, bot_name: str, capability_id: str, parameters: Dict,
                           user_id: str, context: Optional[Dict] = None) -> Dict:
        """Execute a single capability directly (for testing or direct calls).
        
        Args:
            bot_name: Name of the bot
            capability_id: Capability ID to execute
            parameters: Parameters for the capability
            user_id: User ID for permission checking
            context: Optional execution context
        
        Returns:
            Execution result dictionary
        """
        # Validate capability
        capability = self.registry.get_capability(bot_name, capability_id)
        if not capability:
            return {
                'success': False,
                'error': f'Capability "{capability_id}" not found for bot "{bot_name}"'
            }
        
        # Check permissions
        permissions_required = capability.get('permissions_required', [])
        if permissions_required:
            has_permission = False
            for perm_level in permissions_required:
                if self.db.check_permission(user_id, bot_name, capability_id, perm_level):
                    has_permission = True
                    break
            
            if not has_permission:
                return {
                    'success': False,
                    'error': 'Permission denied'
                }
        
        # Execute
        return self._execute_step(bot_name, capability_id, parameters, context)

