import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function FederatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("INTEGRATIONS");
  return children;
}
