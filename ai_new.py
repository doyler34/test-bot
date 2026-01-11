"""
AI/NLP module for parsing natural language and generating structured commands.

This module uses an LLM to:
- Parse user instructions in natural language
- Match instructions to registered capabilities
- Generate structured JSON command definitions
- NEVER executes code, only generates structured data
"""

import json
import os
from typing import List, Dict, Optional
from registry import CapabilityRegistry


class AICommandGenerator:
    """Handles AI-based command generation from natural language."""
    
    def __init__(self, registry: CapabilityRegistry, api_key: Optional[str] = None):
        """Initialize the AI command generator.
        
        Args:
            registry: CapabilityRegistry instance
            api_key: Optional API key for LLM service (defaults to Gemini if available)
        """
        self.registry = registry
        self.api_key = api_key or os.getenv('GEMINI_API_KEY')
        self._setup_llm()
    
    def _setup_llm(self):
        """Set up the LLM client (Gemini by default, can be extended for other providers)."""
        try:
            import google.generativeai
            self.model = google.generativeai.Gemini(api_key=self.api_key) if self.api_key else None
            self.use_google.generativeai = True
        except ImportError:
            self.model = None
            self.use_google.generativeai = False
            print("Warning: Gemini library not installed. AI features will be limited.")
    
    def generate_command_from_natural_language(self, instruction: str, user_id: str) -> Dict:
        """Convert natural language instruction into a structured command.
        
        Args:
            instruction: Natural language instruction from user
            user_id: Discord user ID for logging
        
        Returns:
            Dictionary with:
                - success: bool
                - command_name: str (suggested)
                - description: str
                - steps: List[Dict] (command steps)
                - error: str (if failed)
        """
        # Get all available capabilities
        capabilities = self.registry.get_all_capabilities()
        
        if not capabilities:
            return {
                'success': False,
                'error': 'No capabilities registered. Cannot generate commands.'
            }
        
        # Build capability context for the AI
        capability_context = self._build_capability_context(capabilities)
        
        # Generate structured command using LLM
        if self.use_google.generativeai and self.model:
            return self._generate_with_google.generativeai(instruction, capability_context, user_id)
        else:
            # Fallback: simple pattern matching (for development/testing)
            return self._generate_fallback(instruction, capabilities)
    
    def _build_capability_context(self, capabilities: List[Dict]) -> str:
        """Build a context string describing all available capabilities."""
        context_parts = ["Available bot capabilities:\n"]
        
        # Group by bot
        bots = {}
        for cap in capabilities:
            bot_name = cap['bot_name']
            if bot_name not in bots:
                bots[bot_name] = []
            bots[bot_name].append(cap)
        
        for bot_name, caps in bots.items():
            context_parts.append(f"\nBot: {bot_name}")
            for cap in caps:
                params = cap.get('parameters', {})
                params_str = ", ".join([f"{k}: {v}" for k, v in params.items()]) if params else "none"
                context_parts.append(
                    f"  - {cap['capability_id']}: {cap['description']} "
                    f"(Parameters: {params_str})"
                )
        
        return "\n".join(context_parts)
    
    def _generate_with_google.generativeai(self, instruction: str, capability_context: str, user_id: str) -> Dict:
        """Generate command using Gemini API."""
        prompt = f"""You are a command generator for a Discord bot orchestrator. Your task is to convert natural language instructions into structured JSON command definitions.

{capability_context}

User instruction: "{instruction}"

Generate a structured command definition as JSON with the following format:
{{
    "command_name": "unique_command_name",
    "description": "Human-readable description",
    "steps": [
        {{
            "bot_name": "bot_name",
            "capability_id": "capability_id",
            "parameters": {{"param1": "value1"}}
        }}
    ]
}}

Rules:
1. Only use capabilities that exist in the context above
2. Parameters must match the capability's parameter definitions
3. Steps are executed in order
4. Return ONLY valid JSON, no additional text
5. Never generate code execution steps

JSON:"""
        
        try:
            response = self.model.chat.completions.create(
                model="gpt-4o-mini",  # or "gpt-4" for better accuracy
                messages=[
                    {"role": "system", "content": "You are a command generator that outputs only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=1000
            )
            
            response_text = response.choices[0].message.content.strip()
            # Extract JSON from response (in case there's markdown code blocks)
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            command_def = json.loads(response_text)
            
            # Validate the generated command
            steps = command_def.get('steps', [])
            is_valid, error = self.registry.validate_command_steps(steps)
            
            if not is_valid:
                return {
                    'success': False,
                    'error': f'Generated command validation failed: {error}'
                }
            
            return {
                'success': True,
                'command_name': command_def.get('command_name'),
                'description': command_def.get('description'),
                'steps': steps
            }
        
        except json.JSONDecodeError as e:
            return {
                'success': False,
                'error': f'Failed to parse AI response as JSON: {str(e)}'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'AI generation error: {str(e)}'
            }
    
    def _generate_fallback(self, instruction: str, capabilities: List[Dict]) -> Dict:
        """Fallback command generation using simple pattern matching.
        
        This is a basic implementation for testing without an LLM API.
        """
        instruction_lower = instruction.lower()
        
        # Try to find matching capabilities
        matched_steps = []
        for cap in capabilities:
            if cap['capability_id'].lower() in instruction_lower or cap['description'].lower() in instruction_lower:
                matched_steps.append({
                    'bot_name': cap['bot_name'],
                    'capability_id': cap['capability_id'],
                    'parameters': {}
                })
                break  # Only match first for fallback
        
        if not matched_steps:
            return {
                'success': False,
                'error': 'Could not match instruction to any capabilities. LLM API required for complex commands.'
            }
        
        # Generate a simple command name
        command_name = instruction.lower().replace(' ', '_')[:50]
        
        return {
            'success': True,
            'command_name': command_name,
            'description': f"Generated from: {instruction}",
            'steps': matched_steps
        }
    
    def refine_command(self, command_name: str, instruction: str, existing_steps: List[Dict]) -> Dict:
        """Refine or modify an existing command based on new instruction.
        
        Args:
            command_name: Name of existing command
            instruction: New instruction to refine the command
            existing_steps: Current steps of the command
        
        Returns:
            Updated command definition
        """
        capabilities = self.registry.get_all_capabilities()
        capability_context = self._build_capability_context(capabilities)
        
        existing_steps_str = json.dumps(existing_steps, indent=2)
        
        prompt = f"""Refine an existing command based on new user instruction.

Existing command: {command_name}
Current steps: {existing_steps_str}

{capability_context}

New instruction: "{instruction}"

Generate an updated command definition as JSON. Keep the same structure but update steps based on the new instruction.

JSON:"""
        
        if self.use_google.generativeai and self.model:
            try:
                response = self.model.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You refine commands by outputting only valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=1000
                )
                
                response_text = response.choices[0].message.content.strip()
                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                elif "```" in response_text:
                    response_text = response_text.split("```")[1].split("```")[0].strip()
                
                command_def = json.loads(response_text)
                steps = command_def.get('steps', [])
                is_valid, error = self.registry.validate_command_steps(steps)
                
                if not is_valid:
                    return {'success': False, 'error': f'Validation failed: {error}'}
                
                return {
                    'success': True,
                    'command_name': command_name,
                    'description': command_def.get('description', ''),
                    'steps': steps
                }
            except Exception as e:
                return {'success': False, 'error': str(e)}
        else:
            return {'success': False, 'error': 'LLM API required for command refinement'}
