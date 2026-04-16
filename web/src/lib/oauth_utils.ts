import {
  OAuthBaseCallbackResponse,
  OAuthPrepareAuthorizationResponse,
} from "./types";

export async function prepareOAuthAuthorizationRequest(
  connector: string,
  finalRedirect: string | null
): Promise<OAuthPrepareAuthorizationResponse> {
  let url = `/api/oauth/prepare-authorization-request?connector=${encodeURIComponent(
    connector
  )}`;

  if (finalRedirect) {
    url += `&redirect_on_success=${encodeURIComponent(finalRedirect)}`;
  }

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      connector,
      redirect_on_success: finalRedirect,
    }),
  });

  if (!response.ok) {
    throw new Error(
      `Failed to prepare OAuth authorization request: ${response.status}`
    );
  }

  return (await response.json()) as OAuthPrepareAuthorizationResponse;
}

export async function handleOAuthAuthorizationResponse(
  connector: string,
  code: string,
  state: string
) {
  if (connector !== "google-drive") {
    return;
  }

  return handleOAuthGoogleDriveAuthorizationResponse(code, state);
}

export async function handleFederatedOAuthCallback(
  federatedConnectorId: string,
  code: string,
  state: string
): Promise<OAuthBaseCallbackResponse> {
  const url = `/api/federated/callback?code=${encodeURIComponent(
    code
  )}&state=${encodeURIComponent(state)}`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    let errorDetails = `Failed to handle federated OAuth callback: ${response.status}`;

    try {
      const responseBody = await response.text();
      errorDetails += `\nResponse Body: ${responseBody}`;
    } catch (err) {
      if (err instanceof Error) {
        errorDetails += `\nUnable to read response body: ${err.message}`;
      } else {
        errorDetails += `\nUnable to read response body: Unknown error type`;
      }
    }

    throw new Error(errorDetails);
  }

  const result = await response.json();

  if (!result.success) {
    throw new Error(result.message || "OAuth callback failed");
  }

  return {
    success: true,
    message: result.message || "OAuth authorization successful",
    redirect_on_success: `/admin/federated/${federatedConnectorId}`,
    finalize_url: null,
  };
}

export async function handleOAuthGoogleDriveAuthorizationResponse(
  code: string,
  state: string
): Promise<OAuthBaseCallbackResponse> {
  const url = `/api/oauth/connector/google-drive/callback?code=${encodeURIComponent(
    code
  )}&state=${encodeURIComponent(state)}`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ code, state }),
  });

  if (!response.ok) {
    let errorDetails = `Failed to handle OAuth Google Drive authorization response: ${response.status}`;

    try {
      const responseBody = await response.text();
      errorDetails += `\nResponse Body: ${responseBody}`;
    } catch (err) {
      if (err instanceof Error) {
        errorDetails += `\nUnable to read response body: ${err.message}`;
      } else {
        errorDetails += `\nUnable to read response body: Unknown error type`;
      }
    }

    throw new Error(errorDetails);
  }

  return (await response.json()) as OAuthBaseCallbackResponse;
}
