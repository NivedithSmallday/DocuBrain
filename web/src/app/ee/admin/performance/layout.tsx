import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function PerformanceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("USAGE_ANALYTICS");
  return children;
}
