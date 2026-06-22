"use client";

import { markdown } from "@opal/utils";
import { useSWRConfig } from "swr";
import { useFormikContext } from "formik";
import * as InputLayouts from "@/layouts/input-layouts";
import {
  LLMProviderFormProps,
  LLMProviderName,
  LLMProviderView,
  NVIDIA_DEFAULT_API_BASE,
  NVIDIA_REASONING_EFFORT_CONFIG_KEY,
  NVIDIA_REASONING_ENABLED_CONFIG_KEY,
  NvidiaReasoningEffort,
} from "@/interfaces/llm";
import { fetchNvidiaModels } from "@/lib/llmConfig/svc";
import {
  useInitialValues,
  buildValidationSchema,
  BaseLLMFormValues,
} from "@/sections/modals/llmConfig/utils";
import { submitProvider } from "@/sections/modals/llmConfig/svc";
import { LLMProviderConfiguredSource } from "@/lib/analytics";
import {
  APIBaseField,
  APIKeyField,
  ModelSelectionField,
  DisplayNameField,
  ModelAccessField,
  ModalWrapper,
} from "@/sections/modals/llmConfig/shared";
import Switch from "@/refresh-components/inputs/Switch";
import InputSelect from "@/refresh-components/inputs/InputSelect";
import { toast } from "@/hooks/useToast";
import { refreshLlmProviderCaches } from "@/lib/llmConfig/cache";

interface NvidiaModalValues extends BaseLLMFormValues {
  api_key: string;
  api_base: string;
  custom_config: Record<string, string>;
}

const REASONING_EFFORTS: NvidiaReasoningEffort[] = ["low", "medium", "high"];

function isReasoningEnabled(custom_config: Record<string, string>): boolean {
  return (
    (custom_config[NVIDIA_REASONING_ENABLED_CONFIG_KEY] ?? "").toLowerCase() ===
    "true"
  );
}

// ─── Reasoning controls (persisted into custom_config) ──────────────────────

function ReasoningField() {
  const formikProps = useFormikContext<NvidiaModalValues>();
  const customConfig = formikProps.values.custom_config ?? {};
  const enabled = isReasoningEnabled(customConfig);
  const effort =
    (customConfig[NVIDIA_REASONING_EFFORT_CONFIG_KEY] as
      | NvidiaReasoningEffort
      | undefined) ?? "high";

  const setConfig = (next: Record<string, string>) =>
    formikProps.setFieldValue("custom_config", next);

  return (
    <>
      <InputLayouts.FieldPadder>
        <InputLayouts.Horizontal
          title="Enable Reasoning"
          description="Turn on extended thinking for reasoning-capable NVIDIA models."
        >
          <Switch
            checked={enabled}
            onCheckedChange={(checked) =>
              setConfig({
                ...customConfig,
                [NVIDIA_REASONING_ENABLED_CONFIG_KEY]: checked
                  ? "true"
                  : "false",
                [NVIDIA_REASONING_EFFORT_CONFIG_KEY]: effort,
              })
            }
          />
        </InputLayouts.Horizontal>
      </InputLayouts.FieldPadder>

      {enabled && (
        <InputLayouts.FieldPadder>
          <InputLayouts.Horizontal
            title="Reasoning Effort"
            description="How much effort the model spends reasoning before answering."
          >
            <InputSelect
              value={effort}
              onValueChange={(value) =>
                setConfig({
                  ...customConfig,
                  [NVIDIA_REASONING_EFFORT_CONFIG_KEY]: value,
                })
              }
            >
              <InputSelect.Trigger placeholder="Select reasoning effort" />
              <InputSelect.Content>
                {REASONING_EFFORTS.map((value) => (
                  <InputSelect.Item key={value} value={value}>
                    {value.charAt(0).toUpperCase() + value.slice(1)}
                  </InputSelect.Item>
                ))}
              </InputSelect.Content>
            </InputSelect>
          </InputLayouts.Horizontal>
        </InputLayouts.FieldPadder>
      )}
    </>
  );
}

interface NvidiaModalInternalsProps {
  existingLlmProvider: LLMProviderView | undefined;
  isOnboarding: boolean;
}

function NvidiaModalInternals({
  existingLlmProvider,
  isOnboarding,
}: NvidiaModalInternalsProps) {
  const formikProps = useFormikContext<NvidiaModalValues>();

  const isFetchDisabled = !formikProps.values.api_key;

  const handleFetchModels = async () => {
    const { models, error } = await fetchNvidiaModels({
      api_key: formikProps.values.api_key,
      api_key_changed: true,
      api_base: formikProps.values.api_base || undefined,
      provider_name: existingLlmProvider?.name,
    });
    if (error) {
      throw new Error(error);
    }
    formikProps.setFieldValue("model_configurations", models);
  };

  return (
    <>
      <APIKeyField
        providerName="NVIDIA"
        subDescription={markdown(
          "Paste your NVIDIA API key (starts with `nvapi-`). [Get a key](https://build.nvidia.com/)."
        )}
      />

      <APIBaseField
        optional
        subDescription="Defaults to NVIDIA's hosted endpoint. Override only for a self-hosted NIM."
        placeholder={NVIDIA_DEFAULT_API_BASE}
      />

      <InputLayouts.FieldSeparator />
      <ReasoningField />

      {!isOnboarding && (
        <>
          <InputLayouts.FieldSeparator />
          <DisplayNameField disabled={!!existingLlmProvider} />
        </>
      )}

      <InputLayouts.FieldSeparator />
      <ModelSelectionField
        shouldShowAutoUpdateToggle={false}
        onRefetch={isFetchDisabled ? undefined : handleFetchModels}
      />

      {!isOnboarding && (
        <>
          <InputLayouts.FieldSeparator />
          <ModelAccessField />
        </>
      )}
    </>
  );
}

export default function NvidiaModal({
  variant = "llm-configuration",
  existingLlmProvider,
  shouldMarkAsDefault,
  onOpenChange,
  onSuccess,
}: LLMProviderFormProps) {
  const isOnboarding = variant === "onboarding";
  const { mutate } = useSWRConfig();

  const onClose = () => onOpenChange?.(false);

  const baseInitialValues = useInitialValues(
    isOnboarding,
    LLMProviderName.NVIDIA,
    existingLlmProvider
  );

  // NVIDIA-specific defaults: prefill the hosted endpoint and reasoning config.
  const initialValues: NvidiaModalValues = {
    ...baseInitialValues,
    api_key: existingLlmProvider?.api_key ?? "",
    api_base: existingLlmProvider?.api_base ?? NVIDIA_DEFAULT_API_BASE,
    custom_config: existingLlmProvider?.custom_config ?? {
      [NVIDIA_REASONING_ENABLED_CONFIG_KEY]: "true",
      [NVIDIA_REASONING_EFFORT_CONFIG_KEY]: "high",
    },
  };

  const validationSchema = buildValidationSchema(isOnboarding, {
    apiKey: true,
  });

  return (
    <ModalWrapper
      providerName={LLMProviderName.NVIDIA}
      llmProvider={existingLlmProvider}
      onClose={onClose}
      initialValues={initialValues}
      description="Connect to NVIDIA's hosted open models (NVIDIA NIM) over their OpenAI-compatible API."
      validationSchema={validationSchema}
      onSubmit={async (values, { setSubmitting, setStatus }) => {
        await submitProvider({
          analyticsSource: isOnboarding
            ? LLMProviderConfiguredSource.CHAT_ONBOARDING
            : LLMProviderConfiguredSource.ADMIN_PAGE,
          providerName: LLMProviderName.NVIDIA,
          values,
          initialValues,
          existingLlmProvider,
          shouldMarkAsDefault,
          setStatus,
          setSubmitting,
          onClose,
          onSuccess: async () => {
            if (onSuccess) {
              await onSuccess();
            } else {
              await refreshLlmProviderCaches(mutate);
              toast.success(
                existingLlmProvider
                  ? "Provider updated successfully!"
                  : "Provider enabled successfully!"
              );
            }
          },
        });
      }}
    >
      <NvidiaModalInternals
        existingLlmProvider={existingLlmProvider}
        isOnboarding={isOnboarding}
      />
    </ModalWrapper>
  );
}
