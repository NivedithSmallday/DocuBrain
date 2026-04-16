import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function Groups2Layout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("ORG_MANAGEMENT");
  return children;
}
