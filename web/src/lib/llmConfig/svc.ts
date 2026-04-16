/**
 * LLM action functions for mutations and model fetching.
 *
 * These are async functions for one-off actions that don't need SWR caching.
 *
 * Endpoints:
 * - /api/admin/llm/test/default - Test the default LLM provider connection
 * - /api/admin/llm/default - Set the default LLM model
 * - /api/admin/llm/provider/{id} - Delete an LLM provider
 * - /api/admin/llm/{provider}/available-models - Fetch available models for a provider
 */

import {
  LLM_ADMIN_URL,
  LLM_PROVIDERS_ADMIN_URL,
} from "@/lib/llmConfig/constants";
import {
  OllamaModelResponse,
  ModelConfiguration,
  LLMProviderName,
  OllamaFetchParams,
  OpenAICompatibleFetchParams,
  OpenAICompatibleModelResponse,
} from "@/interfaces/llm";

/**
 * Test the default LLM provider.
 * Returns true if the default provider is configured and working, false otherwise.
 */
export async function testDefaultProvider(): Promise<boolean> {
  try {
    const response = await fetch(`${LLM_ADMIN_URL}/test/default`, {
      method: "POST",
    });
    return response?.ok || false;
  } catch {
    return false;
  }
}

/**
 * Set the default LLM model.
 * @param providerId - The provider ID
 * @param modelName - The model name within that provider
 * @throws Error with the detail message from the API on failure
 */
export async function setDefaultLlmModel(
  providerId: number,
  modelName: string
): Promise<void> {
  const response = await fetch(`${LLM_ADMIN_URL}/default`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider_id: providerId,
      model_name: modelName,
    }),
  });

  if (!response.ok) {
    const errorMsg = (await response.json()).detail;
    throw new Error(errorMsg);
  }
}

/**
 * Delete an LLM provider.
 * @param providerId - The provider ID to delete
 * @param force - Force delete even if this is the default provider
 * @throws Error with the detail message from the API on failure
 */
export async function deleteLlmProvider(
  providerId: number,
  force = false
): Promise<void> {
  const url = force
    ? `${LLM_PROVIDERS_ADMIN_URL}/${providerId}?force=true`
    : `${LLM_PROVIDERS_ADMIN_URL}/${providerId}`;
  const response = await fetch(url, { method: "DELETE" });

  if (!response.ok) {
    const errorMsg = (await response.json()).detail;
    throw new Error(errorMsg);
  }
}

// ---------------------------------------------------------------------------
// Aggregator providers & helpers
// ---------------------------------------------------------------------------

/** Aggregator providers that host models from multiple vendors. */
export const AGGREGATOR_PROVIDERS = new Set([
  "ollama_chat",
  "openai_compatible",
]);

export const isAnthropic = (_provider: string, modelName?: string) =>
  !!modelName?.toLowerCase().includes("claude");

// ---------------------------------------------------------------------------
// Model fetching
// ---------------------------------------------------------------------------

/**
 * Fetches Ollama models directly without any form state dependencies.
 * Uses snake_case params to match API structure.
 */
export const fetchOllamaModels = async (
  params: OllamaFetchParams
): Promise<{ models: ModelConfiguration[]; error?: string }> => {
  const apiBase = params.api_base;
  if (!apiBase) {
    return { models: [], error: "API Base is required" };
  }

  try {
    const response = await fetch("/api/admin/llm/ollama/available-models", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        api_base: apiBase,
        provider_name: params.provider_name,
      }),
      signal: params.signal,
    });

    if (!response.ok) {
      let errorMessage = "Failed to fetch models";
      try {
        const errorData = await response.json();
        errorMessage = errorData.detail || errorData.message || errorMessage;
      } catch {
        // ignore JSON parsing errors
      }
      return { models: [], error: errorMessage };
    }

    const data: OllamaModelResponse[] = await response.json();
    const models: ModelConfiguration[] = data.map((modelData) => ({
      name: modelData.name,
      display_name: modelData.display_name,
      is_visible: true,
      max_input_tokens: modelData.max_input_tokens,
      supports_image_input: modelData.supports_image_input,
      supports_reasoning: false,
    }));

    return { models };
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    return { models: [], error: errorMessage };
  }
};

/**
 * Fetches models from a generic OpenAI-compatible server.
 * Uses snake_case params to match API structure.
 */
export const fetchOpenAICompatibleModels = async (
  params: OpenAICompatibleFetchParams
): Promise<{ models: ModelConfiguration[]; error?: string }> => {
  const apiBase = params.api_base;
  if (!apiBase) {
    return { models: [], error: "API Base is required" };
  }

  try {
    const response = await fetch(
      "/api/admin/llm/openai-compatible/available-models",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          api_base: apiBase,
          api_key: params.api_key,
          provider_name: params.provider_name,
        }),
        signal: params.signal,
      }
    );

    if (!response.ok) {
      let errorMessage = "Failed to fetch models";
      try {
        const errorData = await response.json();
        errorMessage = errorData.detail || errorData.message || errorMessage;
      } catch {
        // ignore JSON parsing errors
      }
      return { models: [], error: errorMessage };
    }

    const data: OpenAICompatibleModelResponse[] = await response.json();
    const models: ModelConfiguration[] = data.map((modelData) => ({
      name: modelData.name,
      display_name: modelData.display_name,
      is_visible: true,
      max_input_tokens: modelData.max_input_tokens,
      supports_image_input: modelData.supports_image_input,
      supports_reasoning: modelData.supports_reasoning,
    }));

    return { models };
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    return { models: [], error: errorMessage };
  }
};

/**
 * Fetches models for a provider. Accepts form values directly and maps them
 * to the expected fetch params format internally.
 */
export const fetchModels = async (
  providerName: string,
  formValues: {
    api_base?: string;
    api_key?: string;
    api_key_changed?: boolean;
    name?: string;
    custom_config?: Record<string, string>;
    model_configurations?: ModelConfiguration[];
  },
  signal?: AbortSignal
) => {
  switch (providerName) {
    case LLMProviderName.OLLAMA_CHAT:
      return fetchOllamaModels({
        api_base: formValues.api_base,
        provider_name: formValues.name,
        signal,
      });
    case LLMProviderName.OPENAI_COMPATIBLE:
      return fetchOpenAICompatibleModels({
        api_base: formValues.api_base,
        api_key: formValues.api_key,
        provider_name: formValues.name,
        signal,
      });
    default:
      return { models: [], error: `Unknown provider: ${providerName}` };
  }
};
