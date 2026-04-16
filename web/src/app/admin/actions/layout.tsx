import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function ActionsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("AGENTS");
  return children;
}
