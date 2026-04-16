// =============================================================================
// LLM Selection Types and Utilities
// =============================================================================

export interface BuildLlmSelection {
  providerName: string;
  provider: string;
  modelName: string;
}

const LLM_SELECTION_PRIORITY = [
  { provider: "ollama", modelName: "llama3.1" },
  {
    provider: "openai_compatible",
    modelName: "meta-llama/Llama-3.1-8B-Instruct",
  },
] as const;

// Minimal provider interface for selection logic
interface MinimalLlmProvider {
  name: string;
  provider: string;
  model_configurations: { name: string; is_visible: boolean }[];
}

/**
 * Get the best default LLM selection based on available providers.
 * Priority: Ollama > vLLM > first available
 */
export function getDefaultLlmSelection(
  llmProviders: MinimalLlmProvider[] | undefined
): BuildLlmSelection | null {
  if (!llmProviders || llmProviders.length === 0) return null;

  // Try each priority provider in order
  for (const { provider, modelName } of LLM_SELECTION_PRIORITY) {
    const matchingProvider = llmProviders.find((p) => p.provider === provider);
    if (matchingProvider) {
      return {
        providerName: matchingProvider.name,
        provider: matchingProvider.provider,
        modelName,
      };
    }
  }

  // Fallback: first available provider, use its first visible model
  const firstProvider = llmProviders[0];
  if (firstProvider) {
    const firstModel = firstProvider.model_configurations.find(
      (m) => m.is_visible
    );
    return {
      providerName: firstProvider.name,
      provider: firstProvider.provider,
      modelName: firstModel?.name ?? "",
    };
  }

  return null;
}

// Recommended models config (for UI display)
export const RECOMMENDED_BUILD_MODELS = {
  preferred: {
    provider: "ollama",
    modelName: "llama3.1",
    displayName: "Llama 3.1",
  },
  alternatives: [
    { provider: "ollama", modelName: "qwen2.5" },
    {
      provider: "openai_compatible",
      modelName: "meta-llama/Llama-3.1-8B-Instruct",
    },
    {
      provider: "openai_compatible",
      modelName: "Qwen/Qwen2.5-7B-Instruct",
    },
  ],
} as const;

// Cookie utilities
const BUILD_LLM_COOKIE_KEY = "build_llm_selection";

export function getBuildLlmSelection(): BuildLlmSelection | null {
  if (typeof document === "undefined") return null;
  const cookie = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${BUILD_LLM_COOKIE_KEY}=`));
  if (!cookie) return null;
  try {
    const value = cookie.split("=")[1];
    if (!value) return null;
    return JSON.parse(decodeURIComponent(value));
  } catch {
    return null;
  }
}

export function setBuildLlmSelection(selection: BuildLlmSelection): void {
  if (typeof document === "undefined") return;
  const value = encodeURIComponent(JSON.stringify(selection));
  // Cookie expires in 1 year
  const expires = new Date(
    Date.now() + 365 * 24 * 60 * 60 * 1000
  ).toUTCString();
  document.cookie = `${BUILD_LLM_COOKIE_KEY}=${value}; path=/; expires=${expires}; SameSite=Lax`;
}

export function clearBuildLlmSelection(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${BUILD_LLM_COOKIE_KEY}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
}

export function isRecommendedModel(
  provider: string,
  modelName: string
): boolean {
  const { preferred, alternatives } = RECOMMENDED_BUILD_MODELS;
  // Exact match for preferred model
  if (preferred.provider === provider && modelName === preferred.modelName) {
    return true;
  }
  // Exact match for alternatives
  return alternatives.some(
    (alt) => alt.provider === provider && modelName === alt.modelName
  );
}

// Curated providers for Build mode (shared between BuildOnboardingModal and BuildLLMPopover)
export interface BuildModeModel {
  name: string;
  label: string;
  recommended?: boolean;
}

export interface BuildModeProvider {
  key: string;
  label: string;
  providerName: string;
  recommended?: boolean;
  models: BuildModeModel[];
  // API-related fields (optional, only needed for onboarding modal)
  apiKeyPlaceholder?: string;
  apiKeyUrl?: string;
  apiKeyLabel?: string;
}

export const BUILD_MODE_PROVIDERS: BuildModeProvider[] = [
  {
    key: "ollama",
    label: "Ollama",
    providerName: "ollama",
    recommended: true,
    models: [
      { name: "llama3.1", label: "Llama 3.1", recommended: true },
      { name: "qwen2.5", label: "Qwen 2.5" },
    ],
    apiKeyPlaceholder: "http://host.docker.internal:11434",
    apiKeyUrl: "https://ollama.com/download",
    apiKeyLabel: "Ollama",
  },
  {
    key: "vllm",
    label: "vLLM",
    providerName: "openai_compatible",
    models: [
      {
        name: "meta-llama/Llama-3.1-8B-Instruct",
        label: "Llama 3.1 8B",
        recommended: true,
      },
      { name: "Qwen/Qwen2.5-7B-Instruct", label: "Qwen 2.5 7B" },
    ],
    apiKeyPlaceholder: "http://host.docker.internal:8000/v1",
    apiKeyUrl: "https://docs.vllm.ai/",
    apiKeyLabel: "vLLM",
  },
];

// =============================================================================
// User Info/Persona Constants
// =============================================================================

export interface PersonaInfo {
  name: string;
  email: string;
}

// Work area enum - derived from PERSONA_MAPPING keys
export enum WorkArea {
  ENGINEERING = "engineering",
  PRODUCT = "product",
  EXECUTIVE = "executive",
  SALES = "sales",
  MARKETING = "marketing",
  OTHER = "other",
}

// Level enum - derived from PERSONA_MAPPING structure
export enum Level {
  IC = "ic",
  MANAGER = "manager",
}

// Persona mapping: work_area -> level -> PersonaInfo
// Matches backend/docubrain/server/features/build/sandbox/util/persona_mapping.py
// This is the source of truth for work areas and levels
export const PERSONA_MAPPING: Record<WorkArea, Record<Level, PersonaInfo>> = {
  [WorkArea.ENGINEERING]: {
    [Level.IC]: {
      name: "Jiwon Kang",
      email: "jiwon_kang@netherite-extraction.docubrain.app",
    },
    [Level.MANAGER]: {
      name: "Javier Morales",
      email: "javier_morales@netherite-extraction.docubrain.app",
    },
  },
  [WorkArea.SALES]: {
    [Level.IC]: {
      name: "Megan Foster",
      email: "megan_foster@netherite-extraction.docubrain.app",
    },
    [Level.MANAGER]: {
      name: "Valeria Cruz",
      email: "valeria_cruz@netherite-extraction.docubrain.app",
    },
  },
  [WorkArea.PRODUCT]: {
    [Level.IC]: {
      name: "Michael Anderson",
      email: "michael_anderson@netherite-extraction.docubrain.app",
    },
    [Level.MANAGER]: {
      name: "David Liu",
      email: "david_liu@netherite-extraction.docubrain.app",
    },
  },
  [WorkArea.MARKETING]: {
    [Level.IC]: {
      name: "Rahul Patel",
      email: "rahul_patel@netherite-extraction.docubrain.app",
    },
    [Level.MANAGER]: {
      name: "Olivia Reed",
      email: "olivia_reed@netherite-extraction.docubrain.app",
    },
  },
  [WorkArea.EXECUTIVE]: {
    [Level.IC]: {
      name: "Sarah Mitchell",
      email: "sarah_mitchell@netherite-extraction.docubrain.app",
    },
    [Level.MANAGER]: {
      name: "Sarah Mitchell",
      email: "sarah_mitchell@netherite-extraction.docubrain.app",
    },
  },
  [WorkArea.OTHER]: {
    [Level.MANAGER]: {
      name: "Ralf Schroeder",
      email: "ralf_schroeder@netherite-extraction.docubrain.app",
    },
    [Level.IC]: {
      name: "John Carpenter",
      email: "john_carpenter@netherite-extraction.docubrain.app",
    },
  },
};

// Helper to capitalize first letter
const capitalize = (str: string): string => {
  return str.charAt(0).toUpperCase() + str.slice(1);
};

// Derive WORK_AREA_OPTIONS from WorkArea enum
export const WORK_AREA_OPTIONS = Object.values(WorkArea).map((value) => ({
  value,
  label: capitalize(value),
}));

// Derive LEVEL_OPTIONS from Level enum
export const LEVEL_OPTIONS = Object.values(Level).map((value) => ({
  value,
  label: value === Level.IC ? "IC" : capitalize(value),
}));

// Work areas where level selection is required
// Executive has the same persona for both levels, so level is optional
export const WORK_AREAS_REQUIRING_LEVEL: WorkArea[] = [
  WorkArea.ENGINEERING,
  WorkArea.PRODUCT,
  WorkArea.SALES,
  WorkArea.MARKETING,
  WorkArea.OTHER,
];

// Helper function to get persona info
export function getPersonaInfo(
  workArea: WorkArea,
  level: Level
): PersonaInfo | undefined {
  return PERSONA_MAPPING[workArea]?.[level];
}

// Company name for demo personas
export const DEMO_COMPANY_NAME = "Netherite Extraction Inc.";

// Helper function to get position text from work area and level
// Executive: "Executive" (no level), Other: "employee", Everything else: show level if available
export function getPositionText(
  workArea: WorkArea,
  level: Level | undefined
): string {
  const workAreaLabel =
    WORK_AREA_OPTIONS.find((opt) => opt.value === workArea)?.label || workArea;

  if (workArea === WorkArea.OTHER) {
    return "Employee";
  }

  if (workArea === WorkArea.EXECUTIVE) {
    return "Executive";
  }

  if (level) {
    const levelLabel =
      LEVEL_OPTIONS.find((opt) => opt.value === level)?.label || level;
    return `${workAreaLabel} ${levelLabel}`;
  }

  return workAreaLabel;
}

export const BUILD_USER_PERSONA_COOKIE_NAME = "build_user_persona";

// Helper type for the consolidated cookie
export interface BuildUserPersona {
  workArea: WorkArea;
  level?: Level;
}

// Helper functions for getting/setting the consolidated cookie
export function getBuildUserPersona(): BuildUserPersona | null {
  if (typeof window === "undefined") return null;

  const cookieValue = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${BUILD_USER_PERSONA_COOKIE_NAME}=`))
    ?.split("=")[1];

  if (!cookieValue) return null;

  try {
    const parsed = JSON.parse(decodeURIComponent(cookieValue));
    // Validate and cast to enum types
    if (
      parsed.workArea &&
      Object.values(WorkArea).includes(parsed.workArea as WorkArea)
    ) {
      return {
        workArea: parsed.workArea as WorkArea,
        level:
          parsed.level && Object.values(Level).includes(parsed.level as Level)
            ? (parsed.level as Level)
            : undefined,
      };
    }
    return null;
  } catch {
    return null;
  }
}

export function setBuildUserPersona(persona: BuildUserPersona): void {
  const cookieValue = encodeURIComponent(JSON.stringify(persona));
  const expires = new Date();
  expires.setFullYear(expires.getFullYear() + 1);
  document.cookie = `${BUILD_USER_PERSONA_COOKIE_NAME}=${cookieValue}; path=/; expires=${expires.toUTCString()}`;
}
