import type { OnboardingActions } from "@/interfaces/onboarding";

export enum LLMProviderName {
  OLLAMA_CHAT = "ollama_chat",
  OPENAI_COMPATIBLE = "openai_compatible",
  NVIDIA = "nvidia",
  CUSTOM = "custom",
}

/** NVIDIA hosted endpoint default (OpenAI-compatible). */
export const NVIDIA_DEFAULT_API_BASE = "https://integrate.api.nvidia.com/v1";
export const NVIDIA_DEFAULT_MODEL = "deepseek-ai/deepseek-v4-flash";

/** custom_config keys used to persist NVIDIA reasoning settings. */
export const NVIDIA_REASONING_ENABLED_CONFIG_KEY = "NVIDIA_REASONING_ENABLED";
export const NVIDIA_REASONING_EFFORT_CONFIG_KEY = "NVIDIA_REASONING_EFFORT";

export type NvidiaReasoningEffort = "low" | "medium" | "high";

export interface ModelConfiguration {
  name: string;
  is_visible: boolean;
  max_input_tokens: number | null;
  supports_image_input: boolean;
  supports_reasoning: boolean;
  display_name?: string;
  provider_display_name?: string;
  vendor?: string;
  version?: string;
  region?: string;
}

export interface SimpleKnownModel {
  name: string;
  display_name: string | null;
}

export interface WellKnownLLMProviderDescriptor {
  name: string;
  known_models: ModelConfiguration[];
  recommended_default_model: SimpleKnownModel | null;
}

export interface LLMModelDescriptor {
  modelName: string;
  provider: string;
  maxTokens: number;
}

export interface LLMProviderView {
  id: number;
  name: string;
  provider: string;
  api_key: string | null;
  api_base: string | null;
  api_version: string | null;
  custom_config: { [key: string]: string } | null;
  is_public: boolean;
  is_auto_mode: boolean;
  groups: number[];
  personas: number[];
  deployment_name: string | null;
  model_configurations: ModelConfiguration[];
}

export interface VisionProvider extends LLMProviderView {
  vision_models: string[];
}

export interface LLMProviderDescriptor {
  id: number;
  name: string;
  provider: string;
  provider_display_name: string;
  model_configurations: ModelConfiguration[];
}

export interface OllamaModelResponse {
  name: string;
  display_name: string;
  max_input_tokens: number | null;
  supports_image_input: boolean;
}

export interface DefaultModel {
  provider_id: number;
  model_name: string;
}

export interface LLMProviderResponse<T> {
  providers: T[];
  default_text: DefaultModel | null;
  default_vision: DefaultModel | null;
}

export type LLMModalVariant = "onboarding" | "llm-configuration";

export interface LLMProviderFormProps {
  variant?: LLMModalVariant;
  existingLlmProvider?: LLMProviderView;
  shouldMarkAsDefault?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Called after successful provider creation/update. */
  onSuccess?: () => void | Promise<void>;

  // Onboarding-specific (only when variant === "onboarding")
  onboardingActions?: OnboardingActions;
}

export interface OllamaFetchParams {
  api_base?: string;
  provider_name?: string;
  signal?: AbortSignal;
}

export interface OpenAICompatibleFetchParams {
  api_base?: string;
  api_key?: string;
  provider_name?: string;
  signal?: AbortSignal;
}

export interface OpenAICompatibleModelResponse {
  name: string;
  display_name: string;
  max_input_tokens: number | null;
  supports_image_input: boolean;
  supports_reasoning: boolean;
}

export interface NvidiaFetchParams {
  api_key?: string;
  api_key_changed?: boolean;
  api_base?: string;
  provider_name?: string;
  signal?: AbortSignal;
}

export interface NvidiaModelResponse {
  name: string;
  display_name: string;
  max_input_tokens: number | null;
  supports_image_input: boolean;
  supports_reasoning: boolean;
}

export type FetchModelsParams =
  | OpenAICompatibleFetchParams
  | OllamaFetchParams
  | NvidiaFetchParams;
