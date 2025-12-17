"""
Módulo de integración con LLM (Gemini)

Nota: Las llamadas a la API se hacen directamente vía REST para usar Structured Outputs.
El GeminiClient solo gestiona API keys y rotación.
"""

from .gemini_client import GeminiClient
from .prompts import get_system_prompt, get_extraction_prompt

__all__ = ["GeminiClient", "get_system_prompt", "get_extraction_prompt"]

