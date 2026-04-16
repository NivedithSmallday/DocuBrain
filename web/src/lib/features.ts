export const FEATURES = {
  AGENTS: false,
  INTEGRATIONS: false,
  ORG_MANAGEMENT: false,
  USAGE_ANALYTICS: false,
  BILLING: false,
} as const;

export type FeatureKey = keyof typeof FEATURES;

export function isFeatureEnabled(feature: FeatureKey): boolean {
  return FEATURES[feature];
}
