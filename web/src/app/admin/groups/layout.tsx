import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function GroupsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("ORG_MANAGEMENT");
  return children;
}
