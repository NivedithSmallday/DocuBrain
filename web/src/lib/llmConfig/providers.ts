import type { IconFunctionComponent } from "@opal/types";
import { SvgCpu, SvgPlug, SvgServer } from "@opal/icons";
import { SvgOllama } from "@opal/logos";
import { LLMProviderName } from "@/interfaces/llm";

export const AGGREGATOR_PROVIDERS = new Set<string>([
  LLMProviderName.OLLAMA_CHAT,
  LLMProviderName.OPENAI_COMPATIBLE,
  LLMProviderName.NVIDIA,
]);

const PROVIDER_ICONS: Record<string, IconFunctionComponent> = {
  [LLMProviderName.OLLAMA_CHAT]: SvgOllama,
  [LLMProviderName.OPENAI_COMPATIBLE]: SvgPlug,
  [LLMProviderName.NVIDIA]: SvgCpu,

  // fallback
  [LLMProviderName.CUSTOM]: SvgServer,
};

const PROVIDER_PRODUCT_NAMES: Record<string, string> = {
  [LLMProviderName.OLLAMA_CHAT]: "Ollama",
  [LLMProviderName.OPENAI_COMPATIBLE]: "vLLM",
  [LLMProviderName.NVIDIA]: "NVIDIA",

  // fallback
  [LLMProviderName.CUSTOM]: "Custom Models",
};

const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  [LLMProviderName.OLLAMA_CHAT]: "Ollama",
  [LLMProviderName.OPENAI_COMPATIBLE]: "vLLM / OpenAI-Compatible",
  [LLMProviderName.NVIDIA]: "NVIDIA Hosted Open Models",

  // fallback
  [LLMProviderName.CUSTOM]: "models from other LiteLLM-compatible providers",
};

export function getProviderProductName(providerName: string): string {
  return PROVIDER_PRODUCT_NAMES[providerName] ?? providerName;
}

export function getProviderDisplayName(providerName: string): string {
  return PROVIDER_DISPLAY_NAMES[providerName] ?? providerName;
}

export function getProviderIcon(providerName: string): IconFunctionComponent {
  return PROVIDER_ICONS[providerName] ?? SvgCpu;
}

// ---------------------------------------------------------------------------
// Model-aware icon resolver (legacy icon set)
// ---------------------------------------------------------------------------

const MODEL_ICON_MAP: Record<string, IconFunctionComponent> = {
  [LLMProviderName.OLLAMA_CHAT]: SvgOllama,
  [LLMProviderName.OPENAI_COMPATIBLE]: SvgPlug,
  llama: SvgCpu,
  ollama: SvgOllama,
  meta: SvgCpu,
};

/**
 * Model-aware icon resolver that checks both provider name and model name
 * to pick the most specific icon (e.g. Claude icon for a Bedrock Claude model).
 */
export const getModelIcon = (
  providerName: string,
  modelName?: string
): IconFunctionComponent => {
  const lowerProviderName = providerName.toLowerCase();

  // For aggregator providers, prioritise showing the vendor icon based on model name
  if (AGGREGATOR_PROVIDERS.has(lowerProviderName) && modelName) {
    const lowerModelName = modelName.toLowerCase();
    for (const [key, icon] of Object.entries(MODEL_ICON_MAP)) {
      if (lowerModelName.includes(key)) {
        return icon;
      }
    }
  }

  // Check if provider name directly matches an icon
  if (lowerProviderName in MODEL_ICON_MAP) {
    const icon = MODEL_ICON_MAP[lowerProviderName];
    if (icon) {
      return icon;
    }
  }

  // For non-aggregator providers, check if model name contains any of the keys
  if (modelName) {
    const lowerModelName = modelName.toLowerCase();
    for (const [key, icon] of Object.entries(MODEL_ICON_MAP)) {
      if (lowerModelName.includes(key)) {
        return icon;
      }
    }
  }

  // Fallback to CPU icon if no matches
  return SvgCpu;
};
