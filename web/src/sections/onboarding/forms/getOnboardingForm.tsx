import React from "react";
import {
  WellKnownLLMProviderDescriptor,
  LLMProviderName,
  LLMProviderFormProps,
} from "@/interfaces/llm";
import { OnboardingActions, OnboardingState } from "@/interfaces/onboarding";
import OllamaModal from "@/sections/modals/llmConfig/OllamaModal";
import OpenAICompatibleModal from "@/sections/modals/llmConfig/OpenAICompatibleModal";
import NvidiaModal from "@/sections/modals/llmConfig/NvidiaModal";

// Display info for LLM provider cards - title is the product name, displayName is the company/platform
const PROVIDER_DISPLAY_INFO: Record<
  string,
  { title: string; displayName: string }
> = {
  [LLMProviderName.OLLAMA_CHAT]: { title: "Ollama", displayName: "Ollama" },
  [LLMProviderName.OPENAI_COMPATIBLE]: {
    title: "vLLM",
    displayName: "OpenAI-Compatible API",
  },
  [LLMProviderName.NVIDIA]: {
    title: "NVIDIA",
    displayName: "NVIDIA Hosted Open Models",
  },
};

export function getProviderDisplayInfo(providerName: string): {
  title: string;
  displayName: string;
} {
  return (
    PROVIDER_DISPLAY_INFO[providerName] ?? {
      title: providerName,
      displayName: providerName,
    }
  );
}

export interface OnboardingFormProps {
  llmDescriptor?: WellKnownLLMProviderDescriptor;
  isCustomProvider?: boolean;
  onboardingState: OnboardingState;
  onboardingActions: OnboardingActions;
  onOpenChange: (open: boolean) => void;
}

export function getOnboardingForm({
  llmDescriptor,
  isCustomProvider,
  onboardingState,
  onboardingActions,
  onOpenChange,
}: OnboardingFormProps): React.ReactNode {
  const providerName = llmDescriptor?.name ?? LLMProviderName.OLLAMA_CHAT;

  const sharedProps: LLMProviderFormProps = {
    variant: "onboarding" as const,
    shouldMarkAsDefault:
      (onboardingState?.data.llmProviders ?? []).length === 0,
    onboardingActions,
    onOpenChange,
    onSuccess: () => {
      onboardingActions.updateData({
        llmProviders: [
          ...(onboardingState?.data.llmProviders ?? []),
          providerName,
        ],
      });
      onboardingActions.setButtonActive(true);
    },
  };

  if (isCustomProvider || !llmDescriptor) {
    return <OllamaModal {...sharedProps} />;
  }

  switch (llmDescriptor.name) {
    case LLMProviderName.OLLAMA_CHAT:
      return <OllamaModal {...sharedProps} />;
    case LLMProviderName.OPENAI_COMPATIBLE:
      return <OpenAICompatibleModal {...sharedProps} />;
    case LLMProviderName.NVIDIA:
      return <NvidiaModal {...sharedProps} />;
    default:
      return <OllamaModal {...sharedProps} />;
  }
}
