import { LLMProviderName, LLMProviderView } from "@/interfaces/llm";
import OllamaModal from "@/sections/modals/llmConfig/OllamaModal";
import OpenAICompatibleModal from "@/sections/modals/llmConfig/OpenAICompatibleModal";

export function getModalForExistingProvider(
  provider: LLMProviderView,
  onOpenChange?: (open: boolean) => void,
  defaultModelName?: string
) {
  const props = {
    existingLlmProvider: provider,
    onOpenChange,
    defaultModelName,
  };

  switch (provider.provider) {
    case LLMProviderName.OLLAMA_CHAT:
      return <OllamaModal {...props} />;
    case LLMProviderName.OPENAI_COMPATIBLE:
      return <OpenAICompatibleModal {...props} />;
    default:
      return null;
  }
}
