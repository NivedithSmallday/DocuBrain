"use client";

import Link from "next/link";
import ErrorPageLayout from "@/components/errorPages/ErrorPageLayout";
import { Button } from "@opal/components";
import InlineExternalLink from "@/refresh-components/InlineExternalLink";
import { logout } from "@/lib/user";
import { useSettingsContext } from "@/providers/SettingsProvider";
import { ApplicationStatus } from "@/interfaces/settings";
import Text from "@/refresh-components/texts/Text";
import { SvgLock } from "@opal/icons";
import { FEATURES } from "@/lib/features";

const linkClassName = "text-action-link-05 hover:text-action-link-06 underline";

export default function AccessRestricted() {
  const settings = useSettingsContext();
  const canManageUsers = FEATURES.ORG_MANAGEMENT;

  const isSeatLimitExceeded =
    settings.settings.application_status ===
    ApplicationStatus.SEAT_LIMIT_EXCEEDED;

  function getSeatLimitMessage() {
    const { used_seats, seat_count } = settings.settings;
    const counts =
      used_seats != null && seat_count != null
        ? ` (${used_seats} users / ${seat_count} seats)`
        : "";
    return `Your organization has exceeded its licensed seat count${counts}. Access is restricted until the number of users is reduced or your license is upgraded.`;
  }

  const initialModalMessage = isSeatLimitExceeded
    ? getSeatLimitMessage()
    : "Your access to DocuBrain is currently restricted by your administrator.";

  return (
    <ErrorPageLayout>
      <div className="flex items-center gap-2">
        <Text headingH2>Access Restricted</Text>
        <SvgLock className="stroke-status-error-05 w-[1.5rem] h-[1.5rem]" />
      </div>

      <Text text03>{initialModalMessage}</Text>

      {isSeatLimitExceeded ? (
        <>
          <Text text03>
            {canManageUsers ? (
              <>
                If you are an administrator, you can manage users on the{" "}
                <Link className={linkClassName} href="/admin/users">
                  User Management
                </Link>{" "}
                page.
              </>
            ) : (
              "If you are an administrator, please review your organization settings and user access."
            )}
          </Text>

          <div className="flex flex-row gap-2">
            <Button
              onClick={async () => {
                await logout();
                window.location.reload();
              }}
            >
              Log out
            </Button>
          </div>
        </>
      ) : (
        <>
          <Text text03>
            Please contact your system administrator if you believe you should
            still have access.
          </Text>

          <Text text03>
            If you are the administrator, reach out to{" "}
            <a className={linkClassName} href="mailto:support@docubrain.app">
              support@docubrain.app
            </a>{" "}
            for help restoring access.
          </Text>

          <div className="flex flex-row gap-2">
            <Button
              onClick={async () => {
                await logout();
                window.location.reload();
              }}
            >
              Log out
            </Button>
          </div>
        </>
      )}

      <Text text03>
        Need help? Join our{" "}
        <InlineExternalLink
          className={linkClassName}
          href="https://discord.gg/4NA5SbzrWb"
        >
          Discord community
        </InlineExternalLink>{" "}
        for support.
      </Text>
    </ErrorPageLayout>
  );
}
