import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function AddConnectorLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("INTEGRATIONS");
  return children;
}
