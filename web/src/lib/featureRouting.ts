import { redirect } from "next/navigation";
import type { Route } from "next";

import { FeatureKey, isFeatureEnabled } from "@/lib/features";

export function redirectWhenFeatureDisabled(
  feature: FeatureKey,
  href: Route = "/app"
): never | void {
  if (!isFeatureEnabled(feature)) {
    redirect(href);
  }
}
