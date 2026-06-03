/**
 * Service layer for Google Workspace MCP OAuth operations
 */

export interface GoogleWorkspaceOAuthStatus {
  enabled: boolean;
  connected: boolean;
  auth_connected: boolean;
  tools_ready: boolean;
  google_email: string | null;
  mcp_server_id: number | null;
  gmail_server_id: number | null;
  drive_server_id: number | null;
  gmail_tool_count: number;
  drive_tool_count: number;
  drive_docs_indexed: number;
  tool_registration_error: string | null;
  scopes: string[];
  token_expired: boolean;
  refresh_available: boolean;
  mcp_drive_docs_indexed: number;
  mcp_drive_last_indexed: string | null;
  mcp_drive_indexing_status: string | null;
}

export interface MCPDriveIndexingStatus {
  mcp_drive_docs_indexed: number;
  last_indexing_status: string | null;
  last_indexing_time: string | null;
  last_error: string | null;
  new_docs_indexed: number;
  skipped_unchanged: number;
  total_chunks: number;
}

export class GoogleWorkspaceServiceError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "GoogleWorkspaceServiceError";
    this.status = status;
  }
}

/**
 * Get the Google Workspace MCP connection status
 */
export async function getGoogleWorkspaceStatus(): Promise<GoogleWorkspaceOAuthStatus> {
  let response: Response;
  try {
    response = await fetch("/api/google-workspace-oauth/status");
  } catch (error) {
    throw new GoogleWorkspaceServiceError(
      `Network error fetching Google Workspace status: ${error instanceof Error ? error.message : "Unknown"}`,
      0
    );
  }
  if (!response.ok) {
    throw new GoogleWorkspaceServiceError(
      "Failed to fetch Google Workspace status",
      response.status
    );
  }
  return await response.json();
}

/**
 * Initiate Google Workspace OAuth connection flow.
 * This opens the Google consent screen in the current window.
 */
export function connectGoogleWorkspace(): void {
  window.location.href = "/api/google-workspace-oauth/connect";
}

/**
 * Trigger MCP Drive indexing for the current user
 */
export async function triggerDriveMcpIndexing(): Promise<MCPDriveIndexingStatus> {
  let response: Response;
  try {
    response = await fetch("/api/google-workspace-oauth/drive-index", {
      method: "POST",
    });
  } catch (error) {
    throw new GoogleWorkspaceServiceError(
      `Network error triggering Drive indexing: ${error instanceof Error ? error.message : "Unknown"}`,
      0
    );
  }
  if (!response.ok) {
    throw new GoogleWorkspaceServiceError(
      "Failed to trigger Drive indexing",
      response.status
    );
  }
  return await response.json();
}

/**
 * Get the current MCP Drive indexing status
 */
export async function getDriveMcpIndexingStatus(): Promise<MCPDriveIndexingStatus> {
  let response: Response;
  try {
    response = await fetch("/api/google-workspace-oauth/drive-index/status");
  } catch (error) {
    throw new GoogleWorkspaceServiceError(
      `Network error fetching Drive indexing status: ${error instanceof Error ? error.message : "Unknown"}`,
      0
    );
  }
  if (!response.ok) {
    throw new GoogleWorkspaceServiceError(
      "Failed to fetch Drive indexing status",
      response.status
    );
  }
  return await response.json();
}

/**
 * Disconnect Google Workspace MCP servers
 */
export async function disconnectGoogleWorkspace(): Promise<void> {
  let response: Response;
  try {
    response = await fetch("/api/google-workspace-oauth/disconnect", {
      method: "POST",
    });
  } catch (error) {
    throw new GoogleWorkspaceServiceError(
      `Network error disconnecting Google Workspace: ${error instanceof Error ? error.message : "Unknown"}`,
      0
    );
  }
  if (!response.ok) {
    throw new GoogleWorkspaceServiceError(
      "Failed to disconnect Google Workspace",
      response.status
    );
  }
}
