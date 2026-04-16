"""Prompt caching framework for LLM providers.

This module provides a framework for enabling prompt caching across different
LLM providers. It supports both implicit caching (automatic provider-side caching)
and explicit caching (with cache metadata management).
"""

from docubrain.llm.prompt_cache.cache_manager import CacheManager
from docubrain.llm.prompt_cache.cache_manager import generate_cache_key_hash
from docubrain.llm.prompt_cache.models import CacheMetadata
from docubrain.llm.prompt_cache.processor import process_with_prompt_cache
from docubrain.llm.prompt_cache.providers.anthropic import AnthropicPromptCacheProvider
from docubrain.llm.prompt_cache.providers.base import PromptCacheProvider
from docubrain.llm.prompt_cache.providers.factory import get_provider_adapter
from docubrain.llm.prompt_cache.providers.noop import NoOpPromptCacheProvider
from docubrain.llm.prompt_cache.providers.openai import OpenAIPromptCacheProvider
from docubrain.llm.prompt_cache.providers.vertex import VertexAIPromptCacheProvider
from docubrain.llm.prompt_cache.utils import combine_messages_with_continuation
from docubrain.llm.prompt_cache.utils import prepare_messages_with_cacheable_transform

__all__ = [
    "AnthropicPromptCacheProvider",
    "CacheManager",
    "CacheMetadata",
    "combine_messages_with_continuation",
    "generate_cache_key_hash",
    "get_provider_adapter",
    "NoOpPromptCacheProvider",
    "OpenAIPromptCacheProvider",
    "prepare_messages_with_cacheable_transform",
    "process_with_prompt_cache",
    "PromptCacheProvider",
    "VertexAIPromptCacheProvider",
]
