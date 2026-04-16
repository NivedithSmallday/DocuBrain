import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function ConnectorLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("INTEGRATIONS");
  return children;
}
