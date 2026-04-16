import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function EEGroupsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("ORG_MANAGEMENT");
  return children;
}
