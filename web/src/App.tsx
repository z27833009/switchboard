import { useEffect, useState } from "react";

import { ChatPage } from "./pages/ChatPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunListPage } from "./pages/RunListPage";
import { TracePage } from "./pages/TracePage";
import { parseRoute, routeKey } from "./routes";
import type { Route } from "./routes";

function currentRoute(): Route {
  return parseRoute(window.location.hash);
}

export default function App() {
  const [route, setRoute] = useState<Route>(currentRoute);

  useEffect(() => {
    const onHashChange = () => {
      setRoute(currentRoute());
    };
    window.addEventListener("hashchange", onHashChange);
    if (window.location.hash === "") {
      window.location.hash = "#/chat";
    }
    return () => {
      window.removeEventListener("hashchange", onHashChange);
    };
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Switchboard</h1>
          <span className="muted">Phase 1 web UI</span>
        </div>
        <nav className="nav">
          <a href="#/chat" className={route.name === "chat" ? "active" : undefined}>
            Chat
          </a>
          <a href="#/runs" className={route.name === "runs" ? "active" : undefined}>
            Runs
          </a>
        </nav>
      </header>
      <main>
        {route.name === "chat" ? <ChatPage /> : null}
        {route.name === "runs" ? <RunListPage /> : null}
        {route.name === "run" ? <RunDetailPage key={routeKey(route)} runId={route.runId} /> : null}
        {route.name === "trace" ? (
          <TracePage
            key={routeKey(route)}
            runId={route.runId}
            spanId={route.spanId}
            auditId={route.auditId}
          />
        ) : null}
      </main>
    </div>
  );
}
