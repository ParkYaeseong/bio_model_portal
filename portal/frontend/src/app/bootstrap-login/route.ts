import { NextResponse } from "next/server";

// Server-side auto-login for the shared, SSO-gated portal account.
// The shared credentials are read from SERVER-ONLY env vars (no NEXT_PUBLIC_
// prefix), so they are never inlined into the client bundle. The browser calls
// POST /bootstrap-login and receives only a short-lived access token.
export const dynamic = "force-dynamic";

function backendBase(): string {
  const raw =
    process.env.API_PROXY_TARGET ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    "http://127.0.0.1:18121";
  return raw.replace(/\/$/, "");
}

export async function POST() {
  const username = process.env.BOOTSTRAP_LOGIN_USERNAME;
  const password = process.env.BOOTSTRAP_LOGIN_PASSWORD;
  if (!username || !password) {
    return NextResponse.json({ error: "auto-login not configured" }, { status: 404 });
  }

  const body = new URLSearchParams({ username, password, grant_type: "password" });
  let res: Response;
  try {
    res = await fetch(`${backendBase()}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
  } catch {
    return NextResponse.json({ error: "backend unreachable" }, { status: 502 });
  }

  if (!res.ok) {
    return NextResponse.json({ error: "auto-login failed" }, { status: 502 });
  }

  const data = await res.json().catch(() => null);
  if (!data?.access_token) {
    return NextResponse.json({ error: "no token" }, { status: 502 });
  }
  return NextResponse.json({ access_token: data.access_token });
}
