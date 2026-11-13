import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { errorMessage, getJson, isAbortError } from "../api";
import type { SettingsResponse } from "../api";
import { CloseIcon, ShieldIcon } from "./Icons";

type SectionKey =
  | "account"
  | "general"
  | "model"
  | "tools"
  | "agents"
  | "retrieval"
  | "cache";

const SECTIONS: Array<{ key: SectionKey; label: string }> = [
  { key: "account", label: "Account" },
  { key: "general", label: "General" },
  { key: "model", label: "Model" },
  { key: "tools", label: "Tools & MCP" },
  { key: "agents", label: "Agents" },
  { key: "retrieval", label: "Retrieval" },
  { key: "cache", label: "Cache" },
];

function Value({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function BooleanTag({ value, yes, no }: { value: boolean; yes: string; no: string }) {
  return <span className={`tag tag-${value ? "ok" : "neutral"}`}>{value ? yes : no}</span>;
}

function Chips({ values, empty }: { values: string[]; empty: string }) {
  if (values.length === 0) {
    return <span className="muted">{empty}</span>;
  }
  return (
    <span className="inline-chips">
      {values.map((value) => (
        <span key={value} className="tag tag-neutral">
          {value}
        </span>
      ))}
    </span>
  );
}

function SectionBody({ section, data }: { section: SectionKey; data: SettingsResponse }) {
  const { platform, tenant, provider } = data;
  if (section === "account") {
    return (
      <>
        <dl className="kv-grid">
          <Value label="tenant id">
            <code>{tenant.tenant_id}</code>
          </Value>
          <Value label="provider">{tenant.provider}</Value>
          <Value label="agent allowlist">
            <Chips values={tenant.agents_allow} empty="no agents allowed" />
          </Value>
          <Value label="tool allowlist">
            <Chips values={tenant.tools_allow} empty="no tools allowed" />
          </Value>
          <Value label="tools needing approval">
            <Chips
              values={tenant.tools_ask}
              empty="none: an ask tool would need the v2 approval flow"
            />
          </Value>
          <Value label="seller scope">
            {tenant.seller_ids === null ? (
              <span>every seller in the data set</span>
            ) : (
              <Chips values={tenant.seller_ids} empty="no seller in scope" />
            )}
          </Value>
        </dl>
        <p className="muted small">
          Switch workspace tenant from the menu at the bottom of the sidebar. There is no account
          or sign-in in this build, so this page never offers one.
        </p>
      </>
    );
  }

  if (section === "general") {
    return (
      <dl className="kv-grid">
        <Value label="orchestrator">
          <code>{platform.orchestrator}</code>
        </Value>
        <Value label="retriever">
          <code>{platform.retriever}</code>
        </Value>
        <Value label="embedder">
          <code>{platform.embedder}</code>
        </Value>
        <Value label="cache adapter">
          <code>{platform.cache}</code>
        </Value>
        <Value label="context policy">
          <code>{platform.context_policy}</code>
        </Value>
        <Value label="run timeout">{platform.limits.run_timeout_s} s</Value>
        <Value label="tool steps per agent">{platform.limits.max_tool_steps}</Value>
        <Value label="loop steps (agent loop)">{platform.limits.max_loop_steps}</Value>
        <Value label="max plan size">{platform.limits.max_plan_size}</Value>
        <Value label="fallback plan">
          <Chips values={platform.limits.fallback_plan} empty="none" />
        </Value>
        <Value label="rows sent to the model">{platform.limits.model_view_rows}</Value>
        <Value label="context fill ratio">{platform.limits.context_fill}</Value>
        <Value label="session history turns">{platform.limits.history_turns}</Value>
      </dl>
    );
  }

  if (section === "model") {
    return (
      <>
        <dl className="kv-grid">
          <Value label="provider">{provider.name}</Value>
          <Value label="model">
            {provider.model === null ? <span className="muted">not set</span> : <code>{provider.model}</code>}
          </Value>
          <Value label="tools">
            <BooleanTag
              value={provider.capabilities?.tools === true}
              yes="supported"
              no="not supported"
            />
          </Value>
          <Value label="JSON schema output">
            <BooleanTag
              value={provider.capabilities?.json_schema === true}
              yes="supported"
              no="uses a tool call instead"
            />
          </Value>
          <Value label="streaming">
            <BooleanTag
              value={provider.capabilities?.streaming === true}
              yes="supported"
              no="not supported"
            />
          </Value>
          <Value label="reasoning">
            <BooleanTag
              value={provider.capabilities?.reasoning === true}
              yes="supported"
              no="not supported"
            />
          </Value>
          <Value label="context window">
            {provider.capabilities === null
              ? "not declared"
              : `${provider.capabilities.context_window.toLocaleString("en-US")} tokens`}
          </Value>
        </dl>
        <h4 className="settings-subheading">Price</h4>
        {provider.price === null ? (
          <p className="muted small">
            No price row matches this provider and model, so a run records no cost instead of an
            invented zero.
          </p>
        ) : (
          <>
            <dl className="kv-grid">
              <Value label="input per million">${provider.price.input_per_million}</Value>
              <Value label="cached input per million">
                ${provider.price.cached_input_per_million}
              </Value>
              <Value label="output per million">${provider.price.output_per_million}</Value>
              <Value label="verified on">{provider.price.verified_on ?? "not recorded"}</Value>
              <Value label="cost">
                <BooleanTag
                  value={provider.price.no_api_cost}
                  yes="no API cost"
                  no="billed per token"
                />
              </Value>
            </dl>
            <p className="muted small">
              Rates come from <code>config/prices.yaml</code>; a paid row is only accepted with the
              date and source URL it was verified against.
            </p>
          </>
        )}
      </>
    );
  }

  if (section === "tools") {
    if (data.tools.length === 0) {
      return (
        <p className="muted">
          No configured tool source declares a manifest, so there is no tool spec to show.
        </p>
      );
    }
    return (
      <ul className="card-list">
        {data.tools.map((tool) => (
          <li key={tool.wire_name} className="card">
            <div className="card-head">
              <code className="card-title">{tool.wire_name}</code>
              <BooleanTag value={tool.allowed} yes="allowed" no="denied" />
            </div>
            <p className="card-text">{tool.description}</p>
            <p className="card-meta">
              <span className="tag tag-neutral">{tool.risk}</span>
              <span className="tag tag-neutral">
                {tool.tenant_scoped ? "tenant scoped" : "not tenant scoped"}
              </span>
              <span className="muted">name {tool.name}, version {tool.version}</span>
            </p>
          </li>
        ))}
      </ul>
    );
  }

  if (section === "agents") {
    if (data.agents.length === 0) {
      return <p className="muted">No agent is registered in this installation.</p>;
    }
    return (
      <ul className="card-list">
        {data.agents.map((agent) => (
          <li key={agent.name} className="card">
            <div className="card-head">
              <code className="card-title">{agent.name}</code>
              <BooleanTag value={agent.allowed} yes="allowed" no="denied" />
            </div>
            <p className="card-text">{agent.description}</p>
            <p className="card-meta">
              <span className="tag tag-neutral">{agent.cacheable ? "cacheable" : "not cacheable"}</span>
              {agent.needs.length > 0 ? (
                <Chips values={agent.needs} empty="" />
              ) : (
                <span className="muted">no tools or retriever needed</span>
              )}
            </p>
          </li>
        ))}
      </ul>
    );
  }

  if (section === "retrieval") {
    return (
      <dl className="kv-grid">
        <Value label="search">{tenant.retrieval.search}</Value>
        <Value label="chunks per query (k)">{tenant.retrieval.k}</Value>
        <Value label="minimum score">{tenant.retrieval.min_score}</Value>
        <Value label="query rewriting">
          <BooleanTag value={tenant.retrieval.rewrite} yes="enabled" no="disabled" />
        </Value>
        <Value label="rewrite mode">{tenant.retrieval.rewrite_mode}</Value>
        <Value label="platform retriever">
          <code>{platform.retriever}</code>
        </Value>
      </dl>
    );
  }

  return (
    <>
      <dl className="kv-grid">
        <Value label="tenant cache">
          <BooleanTag value={tenant.cache.enabled} yes="enabled" no="disabled" />
        </Value>
        <Value label="similarity threshold">{tenant.cache.threshold}</Value>
        <Value label="entry TTL">{tenant.cache.ttl_days} days</Value>
        <Value label="adapter">
          <code>{platform.cache}</code>
        </Value>
      </dl>
      <p className="muted small">
        {tenant.cache.enabled
          ? "A lookup runs before the supervisor; only a passed, cacheable answer is stored."
          : "The adapter is wired, but this tenant does not look up or store answers in it."}
      </p>
    </>
  );
}

export function SettingsDialog({
  open,
  tenantId,
  onClose,
}: {
  open: boolean;
  tenantId: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const navRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const onCloseRef = useRef(onClose);
  const [section, setSection] = useState<SectionKey>("account");
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [narrow, setNarrow] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches,
  );

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // The section list is vertical on a wide dialog and horizontal when the
  // dialog stacks, so aria-orientation (and the arrow keys it promises) follows
  // the layout instead of always announcing a vertical tablist.
  useEffect(() => {
    const query = window.matchMedia("(max-width: 1023px)");
    const onChange = () => setNarrow(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  // Native modal: the browser makes the page inert, keeps focus inside, closes
  // on Escape and returns focus to the invoker. The dialog is never opened with
  // the `open` attribute or show(), which would lose all of that.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) {
      return;
    }
    if (open && !dialog.open) {
      setSection("account");
      dialog.showModal();
      closeRef.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) {
      return;
    }
    const handleClose = () => onCloseRef.current();
    dialog.addEventListener("close", handleClose);
    return () => dialog.removeEventListener("close", handleClose);
  }, []);

  // `closedby="any"` gives light dismiss where it is supported. Safari does not
  // implement it yet, so this fallback tests the pointer against the dialog's
  // content box: a click on the dialog's own padding must not close it.
  useEffect(() => {
    const dialog = dialogRef.current;
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

  useEffect(() => {
    if (!open) {
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    // Clearing the previous tenant's payload means a slow response never shows
    // another tenant's values under this one's heading.
    setData(null);
    getJson<SettingsResponse>(
      `/settings?tenant_id=${encodeURIComponent(tenantId)}`,
      controller.signal,
    )
      .then((payload) => {
        if (!controller.signal.aborted) {
          setData(payload);
        }
      })
      .catch((caught) => {
        if (!isAbortError(caught)) {
          setData(null);
          setError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [open, tenantId, reloadKey]);

  function selectSection(index: number) {
    const next = SECTIONS[index];
    if (next === undefined) {
      return;
    }
    setSection(next.key);
    navRefs.current[index]?.focus();
  }

  function onNavKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, index: number) {
    const previousKey = narrow ? "ArrowLeft" : "ArrowUp";
    const nextKey = narrow ? "ArrowRight" : "ArrowDown";
    if (event.key === nextKey) {
      event.preventDefault();
      selectSection((index + 1) % SECTIONS.length);
    } else if (event.key === previousKey) {
      event.preventDefault();
      selectSection((index - 1 + SECTIONS.length) % SECTIONS.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      selectSection(0);
    } else if (event.key === "End") {
      event.preventDefault();
      selectSection(SECTIONS.length - 1);
    }
  }

  const activeLabel = SECTIONS.find((item) => item.key === section)?.label ?? "";

  return (
    <dialog
      ref={dialogRef}
      className="settings-dialog"
      aria-labelledby="settings-title"
      closedby="any"
    >
      <div className="settings">
        <header className="settings-header">
          <div>
            <h2 id="settings-title">Settings</h2>
            <p className="muted small">
              tenant <code>{tenantId}</code>
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-button"
            onClick={() => dialogRef.current?.close()}
          >
            <CloseIcon />
            <span className="visually-hidden">Close settings</span>
          </button>
        </header>

        <p className="read-only-note">
          <ShieldIcon className="read-only-icon" />
          <span>
            Read only. These are the values this process loaded at startup; there is no route that
            changes them, and no API key or base URL is sent to the browser.
          </span>
        </p>

        <div className="settings-body">
          <div
            className="settings-nav"
            role="tablist"
            aria-label="Settings sections"
            aria-orientation={narrow ? "horizontal" : "vertical"}
          >
            {SECTIONS.map((item, index) => (
              <button
                key={item.key}
                ref={(element) => {
                  navRefs.current[index] = element;
                }}
                type="button"
                role="tab"
                id={`settings-tab-${item.key}`}
                className="settings-nav-item"
                aria-selected={section === item.key}
                tabIndex={section === item.key ? 0 : -1}
                onClick={() => setSection(item.key)}
                onKeyDown={(event) => onNavKeyDown(event, index)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div
            className="settings-content"
            role="tabpanel"
            id="settings-panel"
            aria-labelledby={`settings-tab-${section}`}
            tabIndex={0}
          >
            <h3 className="settings-section-title">{activeLabel}</h3>
            {loading && data === null ? <p className="muted">Loading settings...</p> : null}
            {error !== null ? (
              <div className="error-box" role="alert">
                <strong>Settings could not be loaded</strong>
                <p>{error}</p>
                <p className="muted small">
                  Nothing is shown rather than a default: the dialog only displays what the API
                  actually returned.
                </p>
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => setReloadKey((value) => value + 1)}
                >
                  Try again
                </button>
              </div>
            ) : null}
            {data !== null ? <SectionBody section={section} data={data} /> : null}
          </div>
        </div>
      </div>
    </dialog>
  );
}
