import { OnboardingStep, FinalStepItemProps } from "@/interfaces/onboarding";
import { SvgCpu, SvgUsers } from "@opal/icons";
import { FEATURES } from "@/lib/features";

type StepConfig = {
  index: number;
  title: string;
  buttonText: string;
  iconPercentage: number;
};

export const STEP_CONFIG: Record<OnboardingStep, StepConfig> = {
  [OnboardingStep.Welcome]: {
    index: 0,
    title: "Let's take a moment to get you set up.",
    buttonText: "Let's Go",
    iconPercentage: 10,
  },
  [OnboardingStep.Name]: {
    index: 1,
    title: "Let's take a moment to get you set up.",
    buttonText: "Next",
    iconPercentage: 40,
  },
  [OnboardingStep.LlmSetup]: {
    index: 2,
    title: "Almost there! Connect your models to start chatting.",
    buttonText: "Next",
    iconPercentage: 70,
  },
  [OnboardingStep.Complete]: {
    index: 3,
    title: "You're all set, review the optional settings or click Finish Setup",
    buttonText: "Finish Setup",
    iconPercentage: 100,
  },
} as const;

export const TOTAL_STEPS = 3;

export const STEP_NAVIGATION: Record<
  OnboardingStep,
  { next?: OnboardingStep; prev?: OnboardingStep }
> = {
  [OnboardingStep.Welcome]: { next: OnboardingStep.Name },
  [OnboardingStep.Name]: {
    next: OnboardingStep.LlmSetup,
    prev: OnboardingStep.Welcome,
  },
  [OnboardingStep.LlmSetup]: {
    next: OnboardingStep.Complete,
    prev: OnboardingStep.Name,
  },
  [OnboardingStep.Complete]: { prev: OnboardingStep.LlmSetup },
};

export const FINAL_SETUP_CONFIG: FinalStepItemProps[] = [
  {
    title: "Connect your models",
    description: "Set up Ollama or vLLM for chat and agents.",
    icon: SvgCpu,
    buttonText: "Language Models",
    buttonHref: "/admin/configuration/llm",
  },
  ...(FEATURES.ORG_MANAGEMENT
    ? [
        {
          title: "Invite your team",
          description: "Manage users and permissions for your team",
          icon: SvgUsers,
          buttonText: "Manage Users",
          buttonHref: "/admin/users",
        },
      ]
    : []),
];
