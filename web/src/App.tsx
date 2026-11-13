import { useCallback, useEffect, useRef, useState } from "react";

import { errorMessage, getHealthState, getJson, isAbortError } from "./api";
import type { HealthState, SessionsResponse, SessionSummary } from "./api";
import { MenuIcon } from "./components/Icons";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar, SidebarRail } from "./components/Sidebar";
import { AppTabs } from "./components/Tabs";
import { TENANTS } from "./config";
import { ChatPage } from "./pages/ChatPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunListPage } from "./pages/RunListPage";
import { TracePage } from "./pages/TracePage";
import { TracesPage } from "./pages/TracesPage";
import { chatHref, parseRoute, routeKey, tabForRoute, tabHref } from "./routes";
import type { Route, TabKey } from "./routes";
import { newSessionId } from "./session";

// How many recent conversations the sidebar shows. Eight keeps the list short
// and every row one click from its stored transcript.
const RECENT_LIMIT = 8;
const HEALTH_REFRESH_MS = 60_000;

function currentRoute(): Route {
  return parseRoute(window.location.hash);
}

export default function App() {
  const [route, setRoute] = useState<Route>(currentRoute);
  const [tenant, setTenant] = useState<string>(() => {
    const initial = currentRoute();
    return initial.tenant ?? TENANTS[0];
  });
  const [activeSessionId, setActiveSessionId] = useState<string | null>(() => {
    const initial = currentRoute();
    return "sessionId" in initial ? initial.sessionId : null;
  });
  const [health, setHealth] = useState<HealthState>("unknown");
  const [recent, setRecent] = useState<SessionSummary[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [recentError, setRecentError] = useState<string | null>(null);
  const [recentKey, setRecentKey] = useState(0);
  const [chatNonce, setChatNonce] = useState(0);
  const [chatTitle, setChatTitle] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const onHashChange = () => {
      const next = parseRoute(window.location.hash);
      setRoute(next);
      if (next.tenant !== null) {
        setTenant(next.tenant);
      }
      if ("sessionId" in next) {
        setActiveSessionId(next.sessionId);
      }
    };
    window.addEventListener("hashchange", onHashChange);
    if (window.location.hash === "") {
      window.location.hash = "#/chat";
    }
    return () => {
      window.removeEventListener("hashchange", onHashChange);
    };
  }, []);

  // A chat URL without a session would make the three tabs ambiguous. Give it
  // one immediately, so Chat/Runs/Traces always describe the same conversation
  // and a reload keeps it.
  useEffect(() => {
    if (route.name !== "chat" || route.sessionId !== null) {
      return;
    }
    const id = newSessionId();
    window.history.replaceState(null, "", chatHref(tenant, id));
    setRoute({ name: "chat", tenant, sessionId: id });
    setActiveSessionId(id);
  }, [route, tenant]);

  useEffect(() => {
    const controller = new AbortController();
    const check = () => {
      // A dependency being down is a normal state for the status line, not a
      // page error, so a failed check only changes the dot and its label.
      getHealthState(controller.signal)
        .then(setHealth)
        .catch(() => undefined);
    };
    check();
    const timer = window.setInterval(check, HEALTH_REFRESH_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setRecentLoading(true);
    setRecentError(null);
    getJson<SessionsResponse>(
      `/sessions?limit=${RECENT_LIMIT}&tenant_id=${encodeURIComponent(tenant)}`,
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) {
          setRecent(data.sessions);
        }
      })
      .catch((caught) => {
        if (!isAbortError(caught)) {
          setRecent([]);
          setRecentError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setRecentLoading(false);
        }
      });
    return () => controller.abort();
  }, [tenant, recentKey]);

  // The drawer is a native modal below 1024 px, which gives focus containment,
  // Escape and focus return for free. Above that width the static sidebar is
  // shown instead, so an open drawer is closed when the viewport grows.
  useEffect(() => {
    const dialog = drawerRef.current;
    if (dialog === null) {
      return;
    }
    if (drawerOpen && !dialog.open) {
      dialog.showModal();
    } else if (!drawerOpen && dialog.open) {
      dialog.close();
    }
  }, [drawerOpen]);

  useEffect(() => {
    const dialog = drawerRef.current;
    if (dialog === null) {
      return;
    }
    const handleClose = () => setDrawerOpen(false);
    dialog.addEventListener("close", handleClose);
    return () => dialog.removeEventListener("close", handleClose);
  }, []);

  useEffect(() => {
    const query = window.matchMedia("(min-width: 1024px)");
    const onChange = () => {
      if (query.matches) {
        setDrawerOpen(false);
      }
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const dialog = drawerRef.current;
    if (dialog === null || "closedBy" in HTMLDialogElement.prototype) {
      return;
    }
    const onClick = (event: MouseEvent) => {
      if (event.target !== dialog) {
        return;
      }
      const rect = dialog.getBoundingClientRect();
      const onContent =
        rect.top <= event.clientY &&
        event.clientY <= rect.bottom &&
        rect.left <= event.clientX &&
        event.clientX <= rect.right;
      if (!onContent) {
        dialog.close();
      }
    };
    dialog.addEventListener("click", onClick);
    return () => dialog.removeEventListener("click", onClick);
  }, []);

  const handleChatTitle = useCallback((title: string | null) => {
    setChatTitle(title);
  }, []);

  const handleRunFinished = useCallback(() => {
    setRecentKey((value) => value + 1);
  }, []);

  const handleSessionChange = useCallback((sessionId: string) => {
    setActiveSessionId(sessionId);
  }, []);

  const switchTenant = useCallback((next: string) => {
    const id = newSessionId();
    setTenant(next);
    setActiveSessionId(id);
    setChatTitle(null);
    setDrawerOpen(false);
    window.location.hash = chatHref(next, id);
  }, []);

  function startNewChat() {
    const id = newSessionId();
    setActiveSessionId(id);
    setChatNonce((value) => value + 1);
    setChatTitle(null);
    setDrawerOpen(false);
    window.location.hash = chatHref(tenant, id);
  }

  function changeTab(tab: TabKey) {
    let sessionId = activeSessionId;
    if (sessionId === null) {
      sessionId = newSessionId();
      setActiveSessionId(sessionId);
    }
    window.location.hash = tabHref(tab, tenant, sessionId);
  }

  function closeSettings() {
    setSettingsOpen(false);
    // In the drawer layout the element that opened the dialog (a menu item
    // inside the drawer) is unmounted with the drawer, so the browser has
    // nothing to return focus to. Put it back on the menu button instead of
    // leaving it on the document. The wider layouts keep the invoker mounted
    // and rely on the browser's own focus return.
    if (window.matchMedia("(max-width: 1023px)").matches) {
      document.querySelector<HTMLElement>(".menu-toggle")?.focus();
    }
  }

  const activeTab = tabForRoute(route);
  const activeKey = activeSessionId;

  let title = "New conversation";
  let subtitle: string | null = null;
  if (route.name === "chat") {
    title = chatTitle ?? "New conversation";
  } else if (route.name === "runs") {
    title = "Runs";
    subtitle = `session ${activeSessionId?.slice(0, 8) ?? "not selected"} · tenant ${tenant}`;
  } else if (route.name === "traces") {
    title = "Traces";
    subtitle = `session ${activeSessionId?.slice(0, 8) ?? "not selected"} · tenant ${tenant}`;
  } else if (route.name === "run") {
    title = "Run detail";
    subtitle = `run ${route.runId.slice(0, 8)} · tenant ${route.tenant ?? tenant}`;
  } else {
    title = "Trace";
    subtitle = `run ${route.runId.slice(0, 8)} · tenant ${route.tenant ?? tenant}`;
  }

  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        Skip to main content
      </a>

      <aside className="sidebar" aria-label="Workspace">
        <Sidebar
          tenant={tenant}
          health={health}
          recent={recent}
          recentLoading={recentLoading}
          recentError={recentError}
          activeKey={activeKey}
          onNewChat={startNewChat}
          onOpenSettings={() => setSettingsOpen(true)}
          onTenantChange={switchTenant}
        />
        <SidebarRail
          tenant={tenant}
          health={health}
          recent={recent}
          activeKey={activeKey}
          onNewChat={startNewChat}
          onOpenSettings={() => setSettingsOpen(true)}
          onTenantChange={switchTenant}
        />
      </aside>

      <div className="app-main">
        <header className="main-header">
          <div className="main-title-row">
            <button
              type="button"
              className="icon-button menu-toggle"
              aria-expanded={drawerOpen}
              aria-controls="app-drawer"
              onClick={() => setDrawerOpen(true)}
            >
              <MenuIcon />
              <span className="visually-hidden">Open navigation</span>
            </button>
            <div className="main-title-text">
              <h1 className="main-title" id="main-content" tabIndex={-1}>
                {title}
              </h1>
              {subtitle !== null ? <p className="main-subtitle">{subtitle}</p> : null}
            </div>
          </div>
          <AppTabs active={activeTab} onChange={changeTab} />
        </header>

        <main className="main-content">
          <div
            className="view-panel"
            role="tabpanel"
            id="app-panel"
            aria-labelledby={`app-tab-${activeTab}`}
            tabIndex={-1}
          >
            {route.name === "chat" ? (
              <ChatPage
                key={`${routeKey(route)}-${chatNonce}`}
                tenant={tenant}
                sessionId={route.sessionId}
                onTitleChange={handleChatTitle}
                onRunFinished={handleRunFinished}
                onSessionChange={handleSessionChange}
              />
            ) : null}
            {route.name === "runs" ? (
              <RunListPage
                key={`${tenant}:${activeSessionId ?? ""}`}
                tenant={tenant}
                sessionId={activeSessionId}
              />
            ) : null}
            {route.name === "traces" ? (
              <TracesPage
                key={`${tenant}:${activeSessionId ?? ""}`}
                tenant={tenant}
                sessionId={activeSessionId}
              />
            ) : null}
            {route.name === "run" ? (
              <RunDetailPage
                key={routeKey(route)}
                runId={route.runId}
                tenant={route.tenant ?? tenant}
                sessionId={activeSessionId}
              />
            ) : null}
            {route.name === "trace" ? (
              <TracePage
                key={routeKey(route)}
                runId={route.runId}
                tenant={route.tenant ?? tenant}
                sessionId={activeSessionId}
                spanId={route.spanId}
                auditId={route.auditId}
                view={route.view}
              />
            ) : null}
          </div>
        </main>
      </div>

      <dialog
        id="app-drawer"
        ref={drawerRef}
        className="drawer"
        aria-label="Navigation"
        closedby="any"
      >
        {drawerOpen ? (
          <Sidebar
            tenant={tenant}
            health={health}
            recent={recent}
            recentLoading={recentLoading}
            recentError={recentError}
            activeKey={activeKey}
            onNewChat={startNewChat}
            onOpenSettings={() => {
              setDrawerOpen(false);
              setSettingsOpen(true);
            }}
            onTenantChange={switchTenant}
            onNavigate={() => setDrawerOpen(false)}
          />
        ) : null}
      </dialog>

      <SettingsDialog
        open={settingsOpen}
        tenantId={tenant}
        onClose={closeSettings}
      />
    </div>
  );
}
