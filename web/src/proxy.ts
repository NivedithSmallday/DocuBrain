import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  AuthType,
  SERVER_SIDE_ONLY__PAID_ENTERPRISE_FEATURES_ENABLED,
  SERVER_SIDE_ONLY__AUTH_TYPE,
} from "./lib/constants";

// NOTE: have to have the "/:path*" here since NextJS doesn't allow any real JS to
// be run before the config is defined e.g. if we try and do a .map it will complain
export const config = {
  matcher: [
    // Auth-protected routes (for middleware auth check)
    "/app/:path*",
    "/admin/:path*",
    "/agents/:path*",
    "/connector/:path*",

    // Enterprise Edition routes (for /ee rewriting)
    // These are ONLY the EE-specific routes that should be rewritten
    "/admin/groups/:path*",
    "/admin/performance/usage/:path*",
    "/admin/performance/query-history/:path*",
    "/admin/theme/:path*",
    "/admin/performance/custom-analytics/:path*",
    "/admin/standard-answer/:path*",
    "/agents/stats/:path*",
  ],
};

// Enterprise Edition specific routes (ONLY these get /ee rewriting)
const EE_ROUTES = [
  "/admin/groups",
  "/admin/performance/usage",
  "/admin/performance/query-history",
  "/admin/theme",
  "/admin/performance/custom-analytics",
  "/admin/standard-answer",
  "/agents/stats",
];

export async function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;

  // NOTE:
  // Authentication is enforced server-side in app/admin layouts via requireAuth
  // and requireAdminAuth. We intentionally avoid edge cookie-based redirects
  // here because cookie transport differences can cause false negatives and
  // redirect loops (/auth/login <-> /app) even when the session is valid.

  // Enterprise Edition: Rewrite EE-specific routes to /ee prefix
  if (SERVER_SIDE_ONLY__PAID_ENTERPRISE_FEATURES_ENABLED) {
    if (EE_ROUTES.some((route) => pathname.startsWith(route))) {
      const newUrl = new URL(`/ee${pathname}`, request.url);
      return NextResponse.rewrite(newUrl);
    }
  }

  return NextResponse.next();
}
