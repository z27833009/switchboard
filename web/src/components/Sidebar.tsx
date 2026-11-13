import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useEffect, useId, useRef, useState } from "react";

import type { HealthState, RunSummary } from "../api";
import { TENANTS } from "../config";
import { formatWhen } from "../format";
import { chatHref } from "../routes";
import { statusTone } from "./Badges";
import {
  ArrowUpRightIcon,
  BookIcon,
  CheckIcon,
  ChevronDownIcon,
  PlusIcon,
  SlidersIcon,
} from "./Icons";

function BrandMark() {
  return (
    <svg
      className="brand-mark"
      width="22"
      height="22"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <rect
        x="2.2"
        y="6.2"
        width="19.6"
        height="11.6"
        rx="4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path d="M12 6.2v11.6" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="7.1" cy="12" r="1.7" fill="var(--accent)" />
      <circle cx="16.9" cy="12" r="1.7" fill="var(--text-secondary)" />
    </svg>
  );
}

function healthLabel(health: HealthState): string {
  if (health === "ok") {
    return "API healthy";
  }
  if (health === "error") {
    return "API error";
  }
  return "Checking API";
}

export function StatusDot({ health }: { health: HealthState }) {
  return (
    <span className="status-line" role="status">
      <span className={`status-dot status-dot-${health}`} aria-hidden="true" />
      <span className="status-text">{healthLabel(health)}</span>
    </span>
  );
}

export function recentHref(run: RunSummary): string {
  if (run.session_id !== null && run.session_id !== "") {
    return chatHref(run.tenant_id, run.session_id);
  }
  return `#/runs/${encodeURIComponent(run.run_id)}?tenant=${encodeURIComponent(run.tenant_id)}`;
}

function recentMeta(run: RunSummary): string {
  const parts = [run.plan ?? "no plan", formatWhen(run.started_at)];
  // A row without a session id cannot resume a conversation, so it says what
  // it does instead: it opens the stored run.
  parts.push(run.session_id === null || run.session_id === "" ? "run" : "conversation");
  return parts.join(" · ");
}

interface SidebarProps {
  tenant: string;
  health: HealthState;
  recent: RunSummary[];
  recentLoading: boolean;
  recentError: string | null;
  activeKey: string | null;
  onNewChat: () => void;
  onOpenSettings: () => void;
  onTenantChange: (tenant: string) => void;
  onNavigate?: () => void;
}

function ProfileMenu({
  tenant,
  onTenantChange,
  onOpenSettings,
  onNavigate,
}: Pick<SidebarProps, "tenant" | "onTenantChange" | "onOpenSettings" | "onNavigate">) {
  const [open, setOpen] = useState(false);
  const [tenantListOpen, setTenantListOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }
      // Escape belongs to a modal dialog when one is open above the menu,
      // otherwise it would close the menu and destroy the element the dialog
      // returns focus to.
      if (!menuRef.current?.contains(document.activeElement)) {
        return;
      }
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const first = menuRef.current?.querySelector<HTMLElement>(
      '[role="menuitem"], [role="menuitemradio"]',
    );
    first?.focus();
  }, [open]);

  function onMenuKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      return;
    }
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitem"], [role="menuitemradio"]',
      ) ?? [],
    );
    if (items.length === 0) {
      return;
    }
    event.preventDefault();
    const current = items.findIndex((item) => item === document.activeElement);
    let next = 0;
    if (event.key === "ArrowDown") {
      next = current < 0 ? 0 : (current + 1) % items.length;
    } else if (event.key === "ArrowUp") {
      next = current <= 0 ? items.length - 1 : current - 1;
    } else if (event.key === "End") {
      next = items.length - 1;
    }
    items[next]?.focus();
  }

  function close() {
    setOpen(false);
    setTenantListOpen(false);
  }

  return (
    <div className="profile">
      <button
        ref={triggerRef}
        type="button"
        className="profile-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="avatar" aria-hidden="true">
          L
        </span>
        <span className="profile-text">
          <span className="profile-title">Local workspace</span>
          <span className="profile-tenant">{tenant}</span>
        </span>
        <ChevronDownIcon className={`profile-chevron${open ? " is-open" : ""}`} />
      </button>
      {open ? (
        <div
          ref={menuRef}
          className="menu"
          role="menu"
          aria-label="Workspace"
          onKeyDown={onMenuKeyDown}
        >
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              // The menu stays open behind the modal so the browser can return
              // focus to this item when the dialog closes; any outside click or
              // Escape afterwards closes it as usual.
              onOpenSettings();
            }}
          >
            <SlidersIcon className="menu-icon" />
            Settings
          </button>
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            aria-expanded={tenantListOpen}
            onClick={() => setTenantListOpen((value) => !value)}
          >
            <span className="menu-icon menu-icon-swap" aria-hidden="true" />
            Switch tenant
            <ChevronDownIcon className={`menu-chevron${tenantListOpen ? " is-open" : ""}`} />
          </button>
          {tenantListOpen ? (
            <div role="group" aria-label="Switch tenant" className="menu-group">
              {TENANTS.map((item) => (
                <button
                  key={item}
                  type="button"
                  role="menuitemradio"
                  aria-checked={item === tenant}
                  className="menu-item menu-item-sub"
                  onClick={() => {
                    close();
                    onTenantChange(item);
                    onNavigate?.();
                  }}
                >
                  <span className="menu-check" aria-hidden="true">
                    {item === tenant ? <CheckIcon /> : null}
                  </span>
                  {item}
                </button>
              ))}
            </div>
          ) : null}
          <a
            role="menuitem"
            className="menu-item"
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
            onClick={() => {
              close();
              onNavigate?.();
            }}
          >
            <BookIcon className="menu-icon" />
            Docs
            <ArrowUpRightIcon className="menu-chevron" />
          </a>
          <p className="menu-note">
            This build has no sign-in. The tenant is a workspace switch, not an account.
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function Sidebar({
  tenant,
  health,
  recent,
  recentLoading,
  recentError,
  activeKey,
  onNewChat,
  onOpenSettings,
  onTenantChange,
  onNavigate,
}: SidebarProps) {
  const recentHeadingId = useId();
  return (
    <div className="sidebar-body">
      <div className="brand-row">
        <a className="brand" href={chatHref(tenant, null)} onClick={onNavigate}>
          <BrandMark />
          <span className="brand-name">Switchboard</span>
        </a>
        <StatusDot health={health} />
      </div>

      <button type="button" className="new-chat" onClick={onNewChat}>
        <PlusIcon className="new-chat-icon" />
        New chat
      </button>

      <nav className="sidebar-section sidebar-recent" aria-labelledby={recentHeadingId}>
        <h2 id={recentHeadingId} className="sidebar-heading">
          Recent
        </h2>
        {recentLoading && recent.length === 0 ? (
          <p className="muted small sidebar-note">Loading recent runs...</p>
        ) : null}
        {recentError !== null ? (
          <p className="muted small sidebar-note">Recent runs could not be loaded. {recentError}</p>
        ) : null}
        {!recentLoading && recentError === null && recent.length === 0 ? (
          <p className="muted small sidebar-note">No runs for this tenant yet.</p>
        ) : null}
        {recent.length > 0 ? (
          <ul className="recent-list">
            {recent.map((run) => {
              const key = `${run.run_id}`;
              return (
                <li key={run.run_id}>
                  <a
                    className={`recent-item${activeKey === key ? " is-active" : ""}`}
                    href={recentHref(run)}
                    onClick={onNavigate}
                  >
                    <span className="recent-query">{run.query}</span>
                    <span className="recent-meta">
                      <span
                        className={`recent-dot recent-dot-${statusTone(run.status)}`}
                        aria-hidden="true"
                      />
                      {recentMeta(run)}
                    </span>
                  </a>
                </li>
              );
            })}
          </ul>
        ) : null}
      </nav>

      <div className="sidebar-footer">
        <ProfileMenu
          tenant={tenant}
          onTenantChange={onTenantChange}
          onOpenSettings={onOpenSettings}
          onNavigate={onNavigate}
        />
      </div>
    </div>
  );
}

// The 1024 to 1279 px layout: a 72 px icon rail. The same actions stay
// reachable, with the accessible name on each control because the label is
// visually hidden.
export function SidebarRail({
  tenant,
  health,
  recent,
  onNewChat,
  onOpenSettings,
  onTenantChange,
  activeKey,
  onNavigate,
}: Omit<SidebarProps, "recentLoading" | "recentError">) {
  const nextTenant =
    TENANTS.length < 2 ? tenant : TENANTS[(TENANTS.indexOf(tenant) + 1) % TENANTS.length];
  return (
    <div className="rail">
      <a
        className="rail-brand"
        href={chatHref(tenant, null)}
        onClick={onNavigate}
        aria-label="Switchboard, new chat"
      >
        <BrandMark />
      </a>
      <button type="button" className="rail-button rail-primary" onClick={onNewChat}>
        <PlusIcon />
        <span className="visually-hidden">New chat</span>
      </button>
      {recent.length > 0 ? (
        <ul className="rail-list" aria-label="Recent">
          {recent.slice(0, 6).map((run) => (
            <li key={run.run_id}>
              <a
                className={`rail-recent${activeKey === run.run_id ? " is-active" : ""}`}
                href={recentHref(run)}
                title={run.query}
                onClick={onNavigate}
              >
                <span aria-hidden="true">{run.query.trim().charAt(0).toUpperCase() || "?"}</span>
                <span className="visually-hidden">{run.query}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="rail-footer">
        <button
          type="button"
          className="rail-recent rail-tenant"
          onClick={() => onTenantChange(nextTenant)}
          aria-label={`Switch tenant, current ${tenant}`}
          title={`Switch tenant (current ${tenant})`}
        >
          <span aria-hidden="true">{tenant.trim().charAt(0).toUpperCase() || "?"}</span>
        </button>
        <button type="button" className="rail-button" onClick={onOpenSettings}>
          <SlidersIcon />
          <span className="visually-hidden">Settings</span>
        </button>
        <span className="rail-status" role="status">
          <span className={`status-dot status-dot-${health}`} aria-hidden="true" />
          <span className="visually-hidden">{healthLabel(health)}</span>
        </span>
      </div>
    </div>
  );
}
