import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";
import HooksPage from "@/ee/refresh-pages/admin/HooksPage";

export default function Hooks() {
  redirectWhenFeatureDisabled("INTEGRATIONS");
  return <HooksPage />;
}
