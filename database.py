"""
Database module for managing capabilities, commands, and audit logs.

This module handles all database interactions including:
- Bot capability registry
- Dynamic command definitions
- Audit logs and permissions
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional, Any
from contextlib import contextmanager
import os


class Database:
    """Handles all database operations for the grandfather bot."""
    
    def __init__(self, db_path: str = "grandfather_bot.db"):
        """Initialize the database connection.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Bot capabilities table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS capabilities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    parameters TEXT,  -- JSON string
                    permissions_required TEXT,  -- JSON string
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(bot_name, capability_id)
                )
            """)
            
            # Custom commands table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS custom_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command_name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    steps TEXT NOT NULL,  -- JSON string of command steps
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    enabled BOOLEAN DEFAULT 1
                )
            """)
            
            # Audit logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_type TEXT NOT NULL,  -- 'command_created', 'command_executed', 'capability_registered', etc.
                    user_id TEXT,
                    username TEXT,
                    details TEXT,  -- JSON string with action details
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    success BOOLEAN DEFAULT 1
                )
            """)
            
            # Permissions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT,
                    bot_name TEXT,  -- NULL means all bots
                    capability_id TEXT,  -- NULL means all capabilities
                    permission_level TEXT NOT NULL,  -- 'read', 'execute', 'admin'
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    # Capability methods
    def register_capability(self, bot_name: str, capability_id: str, description: str,
                           parameters: Optional[Dict] = None,
                           permissions_required: Optional[List[str]] = None) -> bool:
        """Register a new bot capability.
        
        Returns:
            True if successful, False if capability already exists
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO capabilities (bot_name, capability_id, description, parameters, permissions_required)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    bot_name,
                    capability_id,
                    description,
                    json.dumps(parameters or {}),
                    json.dumps(permissions_required or [])
                ))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False
    
    def get_capability(self, bot_name: str, capability_id: str) -> Optional[Dict]:
        """Get a specific capability."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM capabilities
                WHERE bot_name = ? AND capability_id = ?
            """, (bot_name, capability_id))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    
    def get_all_capabilities(self, bot_name: Optional[str] = None) -> List[Dict]:
        """Get all capabilities, optionally filtered by bot_name."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if bot_name:
                cursor.execute("""
                    SELECT * FROM capabilities WHERE bot_name = ?
                    ORDER BY bot_name, capability_id
                """, (bot_name,))
            else:
                cursor.execute("""
                    SELECT * FROM capabilities
                    ORDER BY bot_name, capability_id
                """)
            return [dict(row) for row in cursor.fetchall()]
    
    # Command methods
    def save_custom_command(self, command_name: str, description: str, steps: List[Dict],
                           created_by: str) -> bool:
        """Save a custom command definition.
        
        Args:
            command_name: Unique name for the command
            description: Human-readable description
            steps: List of command steps (dicts with bot_name, capability_id, parameters)
            created_by: User ID who created the command
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO custom_commands (command_name, description, steps, created_by)
                    VALUES (?, ?, ?, ?)
                """, (
                    command_name,
                    description,
                    json.dumps(steps),
                    created_by
                ))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False
    
    def get_custom_command(self, command_name: str) -> Optional[Dict]:
        """Get a custom command by name."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM custom_commands WHERE command_name = ? AND enabled = 1
            """, (command_name,))
            row = cursor.fetchone()
            if row:
                cmd = dict(row)
                cmd['steps'] = json.loads(cmd['steps'])
                return cmd
            return None
    
    def get_all_commands(self) -> List[Dict]:
        """Get all enabled custom commands."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM custom_commands WHERE enabled = 1
                ORDER BY command_name
            """)
            results = []
            for row in cursor.fetchall():
                cmd = dict(row)
                cmd['steps'] = json.loads(cmd['steps'])
                results.append(cmd)
            return results
    
    # Audit log methods
    def log_action(self, action_type: str, user_id: Optional[str] = None,
                   username: Optional[str] = None, details: Optional[Dict] = None,
                   success: bool = True):
        """Log an action to the audit log."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_logs (action_type, user_id, username, details, success)
                VALUES (?, ?, ?, ?, ?)
            """, (
                action_type,
                user_id,
                username,
                json.dumps(details or {}),
                success
            ))
            conn.commit()
    
    def get_logs(self, limit: int = 100, action_type: Optional[str] = None) -> List[Dict]:
        """Get audit logs, optionally filtered by action_type."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if action_type:
                cursor.execute("""
                    SELECT * FROM audit_logs
                    WHERE action_type = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (action_type, limit))
            else:
                cursor.execute("""
                    SELECT * FROM audit_logs
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
            results = []
            for row in cursor.fetchall():
                log = dict(row)
                log['details'] = json.loads(log['details'] or '{}')
                results.append(log)
            return results
    
    # Permission methods
    def check_permission(self, user_id: str, bot_name: str, capability_id: str,
                        permission_level: str) -> bool:
        """Check if a user has permission for a specific capability.
        
        Args:
            user_id: Discord user ID
            bot_name: Name of the bot
            capability_id: Capability ID
            permission_level: Required permission level ('read', 'execute', 'admin')
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Check for specific capability permission
            cursor.execute("""
                SELECT permission_level FROM permissions
                WHERE user_id = ? AND bot_name = ? AND capability_id = ?
            """, (user_id, bot_name, capability_id))
            row = cursor.fetchone()
            if row:
                return self._permission_level_allows(row['permission_level'], permission_level)
            
            # Check for bot-wide permission
            cursor.execute("""
                SELECT permission_level FROM permissions
                WHERE user_id = ? AND bot_name = ? AND capability_id IS NULL
            """, (user_id, bot_name))
            row = cursor.fetchone()
            if row:
                return self._permission_level_allows(row['permission_level'], permission_level)
            
            # Check for global permission (all bots)
            cursor.execute("""
                SELECT permission_level FROM permissions
                WHERE user_id = ? AND bot_name IS NULL
            """, (user_id,))
            row = cursor.fetchone()
            if row:
                return self._permission_level_allows(row['permission_level'], permission_level)
            
            return False
    
    def _permission_level_allows(self, user_level: str, required_level: str) -> bool:
        """Check if user's permission level allows the required level."""
        levels = {'read': 1, 'execute': 2, 'admin': 3}
        return levels.get(user_level, 0) >= levels.get(required_level, 0)
    
    def grant_permission(self, user_id: str, permission_level: str,
                        bot_name: Optional[str] = None,
                        capability_id: Optional[str] = None):
        """Grant a permission to a user."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO permissions (user_id, bot_name, capability_id, permission_level)
                VALUES (?, ?, ?, ?)
            """, (user_id, bot_name, capability_id, permission_level))
            conn.commit()

