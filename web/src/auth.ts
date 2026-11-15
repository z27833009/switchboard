import { errorMessage, getJson, postJson } from "./api";

export interface AuthUser {
  user_id: string;
  email: string;
  display_name: string;
  tenant_id: string;
  role: "admin" | "member";
}

export interface AuthStatus {
  auth_required: boolean;
  authenticated: boolean;
  user: AuthUser | null;
}

export function getAuthStatus(signal?: AbortSignal): Promise<AuthStatus> {
  return getJson<AuthStatus>("/auth/status", signal);
}

export async function getTenants(signal?: AbortSignal): Promise<string[]> {
  const payload = await getJson<{ tenants: string[] }>("/tenants", signal);
  return payload.tenants;
}

export function login(
  email: string,
  password: string,
  tenantId: string,
  signal?: AbortSignal,
): Promise<AuthStatus> {
  return postJson<AuthStatus>(
    "/auth/login",
    { email, password, tenant_id: tenantId },
    signal,
  );
}

export async function logout(signal?: AbortSignal): Promise<AuthStatus> {
  return postJson<AuthStatus>("/auth/logout", {}, signal);
}

export { errorMessage };
