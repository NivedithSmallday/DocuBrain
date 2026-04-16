import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";
import ServiceAccountsPage from "@/refresh-pages/admin/ServiceAccountsPage";

export default function Page() {
  redirectWhenFeatureDisabled("INTEGRATIONS");
  return <ServiceAccountsPage />;
}
