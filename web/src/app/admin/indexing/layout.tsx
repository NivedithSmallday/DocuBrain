import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function IndexingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("INTEGRATIONS");
  return children;
}
