import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";

export default function ThemeLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  redirectWhenFeatureDisabled("ORG_MANAGEMENT");
  return children;
}
