import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function ConnectorsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("INTEGRATIONS");
  return children;
}
