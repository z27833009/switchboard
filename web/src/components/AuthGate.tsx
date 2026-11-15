import type { FormEvent, ReactNode } from "react";
import { useEffect, useState } from "react";

import { errorMessage, getAuthStatus, login } from "../auth";
import type { AuthStatus } from "../auth";
import { TENANTS } from "../config";

function LoginPage({ onLoggedIn }: { onLoggedIn: (status: AuthStatus) => void }) {
  const [tenantId, setTenantId] = useState(TENANTS[0] ?? "default");
  const [email, setEmail] = useState("admin@local.invalid");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLoggedIn(await login(email, password, tenantId));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <h1>Switchboard</h1>
        <p className="muted">Sign in to the local workspace.</p>
        <label className="field">
          <span className="field-label">Tenant</span>
          <select value={tenantId} onChange={(event) => setTenantId(event.target.value)}>
            {TENANTS.map((tenant) => (
              <option key={tenant} value={tenant}>
                {tenant}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Email</span>
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        <button type="submit" className="button button-primary" disabled={busy}>
          {busy ? "Signing in..." : "Sign in"}
        </button>
        {error === null ? null : (
          <p className="error-text small" role="alert">
            {error}
          </p>
        )}
        <p className="muted small">
          The first admin password comes from <code>SWITCHBOARD_ADMIN_PASSWORD</code>.
        </p>
      </form>
    </main>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getAuthStatus(controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) {
          setStatus(payload);
        }
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(caught));
        }
      });
    return () => controller.abort();
  }, []);

  if (error !== null) {
    return (
      <main className="login-shell">
        <div className="error-box" role="alert">
          <strong>The API could not be reached</strong>
          <p>{error}</p>
        </div>
      </main>
    );
  }
  if (status === null) {
    return (
      <main className="login-shell">
        <p className="muted">Checking sign-in...</p>
      </main>
    );
  }
  if (status.auth_required && !status.authenticated) {
    return <LoginPage onLoggedIn={setStatus} />;
  }
  return <>{children}</>;
}
