import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function TokenRateLimitsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("ORG_MANAGEMENT");
  return children;
}
