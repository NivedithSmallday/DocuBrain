import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";
import UsersPage from "@/refresh-pages/admin/UsersPage";

export default function Users() {
  redirectWhenFeatureDisabled("ORG_MANAGEMENT");
  return <UsersPage />;
}
