import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function AgentsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("AGENTS");
  return children;
}
