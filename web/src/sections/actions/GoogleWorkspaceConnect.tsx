"use client";

import { useCallback, useEffect, useState, type ComponentType } from "react";
import { Button, Card, Text } from "@opal/components";
import {
  SvgAlertCircle,
  SvgCheckCircle,
  SvgFileText,
  SvgFolder,
  SvgRefreshCw,
  SvgTextLines,
  SvgUnplug,
  SvgDownload,
} from "@opal/icons";
import { SvgGoogle } from "@opal/logos";

import { toast } from "@/hooks/useToast";
import {
  GoogleWorkspaceOAuthStatus,
  GoogleWorkspaceServiceError,
  connectGoogleWorkspace,
  disconnectGoogleWorkspace,
  getGoogleWorkspaceStatus,
  triggerDriveMcpIndexing,
} from "@/lib/tools/googleWorkspaceService";

interface GoogleWorkspaceConnectProps {
  onConnectionChange?: () => void;
}

const DISABLED_STATUS: GoogleWorkspaceOAuthStatus = {
  enabled: false,
  connected: false,
  auth_connected: false,
  tools_ready: false,
  google_email: null,
  mcp_server_id: null,
  gmail_server_id: null,
  drive_server_id: null,
  gmail_tool_count: 0,
  drive_tool_count: 0,
  drive_docs_indexed: 0,
  tool_registration_error: null,
  scopes: [],
  token_expired: false,
  refresh_available: false,
  mcp_drive_docs_indexed: 0,
  mcp_drive_last_indexed: null,
  mcp_drive_indexing_status: null,
};

export default function GoogleWorkspaceConnect({
  onConnectionChange,
}: GoogleWorkspaceConnectProps) {
  const [status, setStatus] = useState<GoogleWorkspaceOAuthStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isDisconnecting, setIsDisconnecting] = useState(false);
  const [isIndexing, setIsIndexing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const data = await getGoogleWorkspaceStatus();
      setStatus(data);
    } catch (error) {
      if (error instanceof GoogleWorkspaceServiceError && error.status === 404) {
        setStatus(DISABLED_STATUS);
        return;
      }
      setLoadError(
        error instanceof Error
          ? error.message
          : "Failed to fetch Google Workspace status"
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleDisconnect = async () => {
    setIsDisconnecting(true);
    try {
      await disconnectGoogleWorkspace();
      setStatus(enabledDisconnectedStatus(status));
      toast.success("Disconnected successfully");
      await fetchStatus();
      onConnectionChange?.();
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to disconnect Google Workspace"
      );
    } finally {
      setIsDisconnecting(false);
    }
  };

  const handleConnect = () => {
    setIsConnecting(true);
    connectGoogleWorkspace();
  };

  const handleIndexLatest = async () => {
    setIsIndexing(true);
    try {
      const result = await triggerDriveMcpIndexing();
      if (result.last_indexing_status === "failed") {
        toast.error(result.last_error ?? "Drive indexing failed");
      } else {
        const msg = `Indexed ${result.new_docs_indexed} new doc${result.new_docs_indexed === 1 ? "" : "s"}, ${result.skipped_unchanged} unchanged`;
        toast.success(msg);
      }
      await fetchStatus();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Failed to index Drive documents"
      );
    } finally {
      setIsIndexing(false);
    }
  };

  if (isLoading) {
    return (
      <Card padding="md" rounding="sm" background="light" border="solid">
        <div className="flex items-center gap-3" aria-label="Loading Google Workspace status">
          <SvgRefreshCw size={18} className="animate-spin text-text-03" />
          <Text font="main-ui-muted" color="text-03">
            Checking Google Workspace connection
          </Text>
        </div>
      </Card>
    );
  }

  if (!status?.enabled && !loadError) {
    return null;
  }

  const totalTools = (status?.gmail_tool_count ?? 0) + (status?.drive_tool_count ?? 0);
  const isConnected = Boolean(status?.connected);
  const hasRegistrationFailure = Boolean(
    status?.auth_connected && !status?.tools_ready
  );
  const isTokenExpired = Boolean(status?.auth_connected && status?.token_expired);
  const canAutoRefresh = Boolean(isTokenExpired && status?.refresh_available);
  const subtitle = isConnected
    ? isTokenExpired && !canAutoRefresh
      ? "Token expired — please re-authorize to continue using Google tools"
      : `Connected as ${status?.google_email ?? "Google Workspace"}`
    : hasRegistrationFailure
      ? (status?.tool_registration_error ??
        "Google OAuth is connected, but internal MCP tools are unavailable.")
    : "Connect Gmail and Drive for AI-powered email and document tools";

  return (
    <Card padding="fit" rounding="sm" background="light" border="solid">
      <div className="flex flex-col gap-3 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-08 border border-border-01 bg-background-tint-00">
              <SvgGoogle size={22} />
            </div>
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <Text as="h3" font="main-ui-action" color="text-05">
                  Google Workspace
                </Text>
                {isConnected && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-status-success-01 px-2 py-0.5">
                    <SvgCheckCircle size={13} className="text-status-text-success-05" />
                    <Text font="secondary-action" color="text-05">
                      Connected
                    </Text>
                  </span>
                )}
                {hasRegistrationFailure && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-status-warning-01 px-2 py-0.5">
                    <SvgAlertCircle size={13} className="text-status-text-warning-05" />
                    <Text font="secondary-action" color="text-05">
                      Tools unavailable
                    </Text>
                  </span>
                )}
                {isTokenExpired && !canAutoRefresh && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-status-warning-01 px-2 py-0.5">
                    <SvgAlertCircle size={13} className="text-status-text-warning-05" />
                    <Text font="secondary-action" color="text-05">
                      Token expired
                    </Text>
                  </span>
                )}
              </div>
              <Text as="p" font="secondary-body" color="text-03" maxLines={2}>
                {loadError ?? subtitle}
              </Text>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {loadError ? (
              <Button
                icon={SvgRefreshCw}
                prominence="secondary"
                onClick={fetchStatus}
                size="sm"
              >
                Retry
              </Button>
            ) : status?.auth_connected ? (
              <>
                <Button
                  icon={isIndexing ? SvgRefreshCw : SvgDownload}
                  prominence="primary"
                  onClick={handleIndexLatest}
                  disabled={isIndexing || isConnecting || isDisconnecting}
                  size="sm"
                >
                  {isIndexing ? "Indexing..." : "Index Latest"}
                </Button>
                <Button
                  icon={SvgRefreshCw}
                  prominence="secondary"
                  onClick={handleConnect}
                  disabled={isConnecting || isDisconnecting || isIndexing}
                  size="sm"
                >
                  {isConnecting ? "Connecting..." : "Re-authorize"}
                </Button>
                <Button
                  icon={SvgUnplug}
                  variant="danger"
                  prominence="secondary"
                  onClick={handleDisconnect}
                  disabled={isDisconnecting || isConnecting || isIndexing}
                  size="sm"
                >
                  {isDisconnecting ? "Disconnecting..." : "Disconnect"}
                </Button>
              </>
            ) : (
              <Button
                icon={SvgGoogle}
                prominence="primary"
                onClick={handleConnect}
                disabled={isConnecting}
                size="sm"
              >
                {isConnecting ? "Connecting..." : "Connect with Google"}
              </Button>
            )}
          </div>
        </div>

        {loadError && (
          <div className="flex items-center gap-2 border-t border-border-01 pt-3">
            <SvgAlertCircle size={15} className="text-status-text-warning-05" />
            <Text font="secondary-body" color="text-03">
              The MCP list still works; only the Google connection status could not load.
            </Text>
          </div>
        )}

        {status?.auth_connected && (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border-01 pt-3">
            <ServiceStat
              icon={SvgTextLines}
              label={`Gmail: ${status?.gmail_tool_count ?? 0} tool${
                status?.gmail_tool_count === 1 ? "" : "s"
              }`}
            />
            <ServiceStat
              icon={SvgFolder}
              label={`Drive: ${status?.drive_tool_count ?? 0} tool${
                status?.drive_tool_count === 1 ? "" : "s"
              }`}
            />
            <Text font="secondary-body" color="text-03">
              {`${totalTools} total`}
            </Text>
            {(status?.drive_docs_indexed ?? 0) > 0 && (
              <ServiceStat
                icon={SvgFileText}
                label={`${status.drive_docs_indexed.toLocaleString()} doc${
                  status.drive_docs_indexed === 1 ? "" : "s"
                } indexed`}
              />
            )}
            {(status?.mcp_drive_docs_indexed ?? 0) > 0 && (
              <ServiceStat
                icon={SvgDownload}
                label={`${status.mcp_drive_docs_indexed.toLocaleString()} MCP-indexed doc${
                  status.mcp_drive_docs_indexed === 1 ? "" : "s"
                }`}
              />
            )}
            {status?.mcp_drive_last_indexed && (
              <Text font="secondary-body" color="text-03">
                {`Last indexed ${formatRelativeTime(status.mcp_drive_last_indexed)}`}
              </Text>
            )}
            {status?.mcp_drive_indexing_status === "failed" && (
              <span className="inline-flex items-center gap-1">
                <SvgAlertCircle size={13} className="text-status-text-warning-05" />
                <Text font="secondary-body" color="text-03">
                  Last indexing failed
                </Text>
              </span>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function enabledDisconnectedStatus(
  previousStatus: GoogleWorkspaceOAuthStatus | null
): GoogleWorkspaceOAuthStatus {
  return {
    enabled: previousStatus?.enabled ?? true,
    connected: false,
    auth_connected: false,
    tools_ready: false,
    google_email: null,
    mcp_server_id: null,
    gmail_server_id: null,
    drive_server_id: null,
    gmail_tool_count: 0,
    drive_tool_count: 0,
    drive_docs_indexed: 0,
    tool_registration_error: null,
    scopes: [],
    token_expired: false,
    refresh_available: false,
    mcp_drive_docs_indexed: 0,
    mcp_drive_last_indexed: null,
    mcp_drive_indexing_status: null,
  };
}

function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  return `${diffDays}d ago`;
}

function ServiceStat({
  icon: Icon,
  label,
}: {
  icon: ComponentType<{ size?: number; className?: string }>;
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon size={14} className="text-text-03" />
      <Text font="secondary-body" color="text-03">
        {label}
      </Text>
    </span>
  );
}
