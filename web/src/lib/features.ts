export const FEATURES = {
  AGENTS: true,
  INTEGRATIONS: true,
  ORG_MANAGEMENT: true,
  USAGE_ANALYTICS: false,
  BILLING: false,
} as const;

export type FeatureKey = keyof typeof FEATURES;

export function isFeatureEnabled(feature: FeatureKey): boolean {
  return FEATURES[feature];
}
