import { redirectWhenFeatureDisabled } from "@/lib/featureRouting";
import AgentsPage from "@/refresh-pages/admin/AgentsPage";

export default function AdminAgentsPage() {
  redirectWhenFeatureDisabled("AGENTS");
  return <AgentsPage />;
}
