from docubrain.llm.constants import LlmProviderNames

OPENAI_PROVIDER_NAME = "openai"

BEDROCK_PROVIDER_NAME = "bedrock"


OLLAMA_PROVIDER_NAME = "ollama_chat"
OLLAMA_API_KEY_CONFIG_KEY = "OLLAMA_API_KEY"

LM_STUDIO_PROVIDER_NAME = "lm_studio"
LM_STUDIO_API_KEY_CONFIG_KEY = "LM_STUDIO_API_KEY"

LITELLM_PROXY_PROVIDER_NAME = "litellm_proxy"

BIFROST_PROVIDER_NAME = "bifrost"

OPENAI_COMPATIBLE_PROVIDER_NAME = "openai_compatible"

# NVIDIA Hosted Open Models (NVIDIA NIM) — OpenAI-compatible endpoint.
NVIDIA_PROVIDER_NAME = "nvidia"
NVIDIA_DEFAULT_API_BASE = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "deepseek-ai/deepseek-v4-flash"
# Reasoning settings are persisted in the provider's custom_config so they can be
# injected into the NVIDIA `extra_body.chat_template_kwargs` at request time.
NVIDIA_REASONING_ENABLED_CONFIG_KEY = "NVIDIA_REASONING_ENABLED"
NVIDIA_REASONING_EFFORT_CONFIG_KEY = "NVIDIA_REASONING_EFFORT"
NVIDIA_DEFAULT_REASONING_EFFORT = "high"

# Fallback model list used when NVIDIA model discovery is unavailable. These are
# the open models NVIDIA hosts on https://integrate.api.nvidia.com/v1.
NVIDIA_DEFAULT_MODELS: list[str] = [
    "deepseek-ai/deepseek-v4-flash",
    "deepseek-ai/deepseek-r1",
    "meta/llama-3.3-70b-instruct",
    "meta/llama-3.1-405b-instruct",
    "mistralai/mistral-large-2",
    "google/gemma-3",
]

# Providers that use optional Bearer auth from custom_config
PROVIDERS_WITH_SPECIAL_API_KEY_HANDLING: dict[str, str] = {
    LlmProviderNames.OLLAMA_CHAT: OLLAMA_API_KEY_CONFIG_KEY,
    LlmProviderNames.LM_STUDIO: LM_STUDIO_API_KEY_CONFIG_KEY,
}

# OpenRouter
OPENROUTER_PROVIDER_NAME = "openrouter"

ANTHROPIC_PROVIDER_NAME = "anthropic"

AZURE_PROVIDER_NAME = "azure"


VERTEXAI_PROVIDER_NAME = "vertex_ai"
VERTEX_CREDENTIALS_FILE_KWARG = "vertex_credentials"
VERTEX_CREDENTIALS_FILE_KWARG_ENV_VAR_FORMAT = "CREDENTIALS_FILE"
VERTEX_LOCATION_KWARG = "vertex_location"

AWS_REGION_NAME_KWARG = "aws_region_name"
AWS_REGION_NAME_KWARG_ENV_VAR_FORMAT = "AWS_REGION_NAME"
AWS_BEARER_TOKEN_BEDROCK_KWARG_ENV_VAR_FORMAT = "AWS_BEARER_TOKEN_BEDROCK"
AWS_ACCESS_KEY_ID_KWARG = "aws_access_key_id"
AWS_ACCESS_KEY_ID_KWARG_ENV_VAR_FORMAT = "AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY_KWARG = "aws_secret_access_key"
AWS_SECRET_ACCESS_KEY_KWARG_ENV_VAR_FORMAT = "AWS_SECRET_ACCESS_KEY"
