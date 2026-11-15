import type {
  FormEvent,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
} from "react";
import { useEffect, useRef, useState } from "react";

import {
  deleteDocument,
  errorMessage,
  getDocumentChunks,
  getJson,
  isAbortError,
  listDocuments,
  postJson,
  reindexDocument,
  restoreSession,
  saveProviderCredential,
  updateProviderModel,
  updateTenantSettings,
  uploadDocument,
} from "../api";
import type {
  DocumentChunk,
  DocumentSummary,
  SessionAuditResponse,
  SessionAuditRow,
  SessionsResponse,
  SessionSummary,
  SettingsAgent,
  SettingsResponse,
  SettingsTool,
  UsersResponse,
  UserSummary,
} from "../api";
import { ChevronDownIcon, CloseIcon, ShieldIcon } from "./Icons";

type SectionKey =
  | "account"
  | "general"
  | "model"
  | "tools"
  | "agents"
  | "retrieval"
  | "cache"
  | "knowledge"
  | "conversations"
  | "users"
  | "tenants";

const SECTIONS: Array<{ key: SectionKey; label: string }> = [
  { key: "account", label: "Account" },
  { key: "general", label: "General" },
  { key: "model", label: "Model" },
  { key: "tools", label: "Tools & MCP" },
  { key: "agents", label: "Agents" },
  { key: "retrieval", label: "Retrieval" },
  { key: "cache", label: "Cache" },
  { key: "knowledge", label: "Knowledge" },
  { key: "conversations", label: "Conversations" },
  { key: "users", label: "Users" },
  { key: "tenants", label: "Tenants" },
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

function ToolCards({
  tools,
  modeFor,
  onModeChange,
}: {
  tools: SettingsTool[];
  modeFor: (tool: SettingsTool) => "allow" | "ask" | "denied";
  onModeChange: (tool: SettingsTool, mode: "allow" | "ask" | "denied") => void;
}) {
  return (
    <ul className="card-list">
      {tools.map((tool) => {
        const mode = modeFor(tool);
        return (
          <li key={tool.wire_name} className="card">
            <div className="card-head">
              <code className="card-title">{tool.wire_name}</code>
              <select
                className="tool-mode-select"
                value={mode}
                aria-label={`Permission for ${tool.wire_name}`}
                onChange={(event) =>
                  onModeChange(tool, event.target.value as "allow" | "ask" | "denied")
                }
              >
                <option value="denied">denied</option>
                <option value="allow" disabled={tool.risk === "write"}>
                  allow
                </option>
                <option value="ask">ask</option>
              </select>
            </div>
          <p className="card-text">{tool.description}</p>
          <p className="card-meta">
            <span className="tag tag-neutral">{tool.risk}</span>
            <span className="tag tag-neutral">
              {tool.tenant_scoped ? "tenant scoped" : "not tenant scoped"}
            </span>
            <span className="muted">
              name {tool.name}, version {tool.version}
            </span>
          </p>
          </li>
        );
      })}
    </ul>
  );
}

function McpServerForm({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [manifest, setManifest] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const result = await postJson<{ revision: number; note: string }>(
        "/settings/mcp-servers",
        { name, url, manifest },
      );
      setMessage(`Saved revision ${result.revision}. ${result.note}.`);
      setName("");
      setUrl("");
      setManifest("");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="mcp-add" onSubmit={submit}>
      <h4 className="settings-subheading">Add MCP server</h4>
      <p className="muted small">
        The manifest is required until tool discovery lands. The new server is written to the
        database and appears after an API restart.
      </p>
      <label className="field">
        <span className="field-label">Name</span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="crm"
          required
        />
      </label>
      <label className="field">
        <span className="field-label">MCP URL</span>
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="http://host.docker.internal:9000/mcp"
          required
        />
      </label>
      <label className="field">
        <span className="field-label">Manifest path</span>
        <input
          value={manifest}
          onChange={(event) => setManifest(event.target.value)}
          placeholder="mcp_servers/crm/manifest.yaml"
          required
        />
      </label>
      <div className="mcp-add-actions">
        <button type="button" className="button button-quiet" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="button button-primary" disabled={busy}>
          {busy ? "Saving..." : "Add server"}
        </button>
      </div>
      {message === null ? null : <p className="muted small">{message}</p>}
      {error === null ? null : (
        <p className="error-text small" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function KnowledgeSection({ tenantId }: { tenantId: string }) {
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewChunks, setPreviewChunks] = useState<DocumentChunk[]>([]);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState<string | null>(null);
  const [reindexNotice, setReindexNotice] = useState<string | null>(null);
  const [reindexError, setReindexError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDocuments(null);
    setError(null);
    listDocuments(tenantId, controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) {
          setDocuments(payload.documents);
        }
      })
      .catch((caught) => {
        if (!isAbortError(caught)) {
          setError(errorMessage(caught));
        }
      });
    return () => controller.abort();
  }, [tenantId, reload]);

  async function remove(document: DocumentSummary) {
    setBusy(document.document_id);
    setUploadError(null);
    try {
      await deleteDocument(document.document_id, tenantId);
      setConfirming(null);
      setReload((value) => value + 1);
    } catch (caught) {
      setUploadError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function togglePreview(document: DocumentSummary) {
    if (previewId === document.document_id) {
      setPreviewId(null);
      return;
    }
    setPreviewId(document.document_id);
    setPreviewBusy(true);
    setPreviewError(null);
    setPreviewChunks([]);
    try {
      const result = await getDocumentChunks(document.document_id, tenantId);
      setPreviewChunks(result.chunks);
    } catch (caught) {
      setPreviewError(errorMessage(caught));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function upload(file: File) {
    if (!/\.(md|txt)$/i.test(file.name)) {
      setUploadError("Upload a .md or .txt file.");
      return;
    }
    setUploading(true);
    setUploadError(null);
    setUploadNotice(null);
    try {
      const result = await uploadDocument(tenantId, file.name, await file.text());
      setUploadNotice(
        `Revision ${result.index_version}: ${result.added} added, ${result.updated} updated, ${result.chunks_written} chunks written.`,
      );
      setReload((value) => value + 1);
    } catch (caught) {
      setUploadError(errorMessage(caught));
    } finally {
      setUploading(false);
    }
  }

  async function reindex(document: DocumentSummary) {
    setReindexing(document.document_id);
    setReindexNotice(null);
    setReindexError(null);
    try {
      const result = await reindexDocument(document.document_id, tenantId);
      setReindexNotice(
        `Revision ${result.index_version}: re-embedded ${result.chunks_embedded} chunks.`,
      );
      setReload((value) => value + 1);
    } catch (caught) {
      setReindexError(errorMessage(caught));
    } finally {
      setReindexing(null);
    }
  }

  const uploadPanel = (
    <div className="settings-edit">
      <h4 className="settings-subheading">Upload a document</h4>
      <label className="field">
        <span className="field-label">Markdown or text file</span>
        <input
          type="file"
          accept=".md,.txt,text/markdown,text/plain"
          disabled={uploading}
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = "";
            if (file !== undefined) {
              void upload(file);
            }
          }}
        />
      </label>
      <p className="muted small">
        The file is chunked, embedded and upserted by file name. Uploading the same name replaces
        its chunks; other tenants never see it.
      </p>
      {uploading ? <p className="muted small">Embedding chunks...</p> : null}
      {uploadNotice === null ? null : <p className="muted small">{uploadNotice}</p>}
      {uploadError === null ? null : (
        <p className="error-text small" role="alert">
          {uploadError}
        </p>
      )}
    </div>
  );

  if (documents === null && error === null) {
    return <p className="muted">Loading documents...</p>;
  }
  if (error !== null) {
    return (
      <div className="error-box" role="alert">
        <strong>Documents could not be loaded</strong>
        <p>{error}</p>
        <button
          type="button"
          className="button button-quiet"
          onClick={() => setReload((value) => value + 1)}
        >
          Try again
        </button>
      </div>
    );
  }
  if (documents === null || documents.length === 0) {
    return (
      <div>
        {uploadPanel}
        <p className="muted">No document is ingested for this tenant.</p>
      </div>
    );
  }

  return (
    <div>
      {uploadPanel}
      <p className="muted small">
        {documents.length} document{documents.length === 1 ? "" : "s"}. Deleting a document removes
        its chunks and bumps the tenant index version, so the next retrieval rebuilds from what
        remains.
      </p>
      {reindexNotice === null ? null : <p className="muted small">{reindexNotice}</p>}
      {reindexError === null ? null : (
        <p className="error-text small" role="alert">
          {reindexError}
        </p>
      )}
      <ul className="card-list">
        {documents.map((document) => (
          <li key={document.document_id} className="card">
            <div className="card-head">
              <code className="card-title">{document.title}</code>
              <span className="tag tag-neutral">{document.chunks} chunks</span>
            </div>
            <p className="card-text">{document.source}</p>
            <p className="card-meta">
              <span className="muted">
                hash {document.content_hash.slice(0, 12)}, ingested{" "}
                {new Date(document.ingested_at).toLocaleString()}
              </span>
            </p>
            <div className="document-actions">
              <button
                type="button"
                className="button button-quiet"
                onClick={() => void togglePreview(document)}
                aria-expanded={previewId === document.document_id}
              >
                {previewId === document.document_id ? "Hide chunks" : "Preview chunks"}
              </button>
              <button
                type="button"
                className="button button-quiet"
                onClick={() => void reindex(document)}
                disabled={reindexing !== null}
              >
                {reindexing === document.document_id ? "Reindexing..." : "Reindex"}
              </button>
              {confirming === document.document_id ? (
                <>
                  <button
                    type="button"
                    className="button button-quiet"
                    onClick={() => setConfirming(null)}
                    disabled={busy !== null}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button button-danger"
                    onClick={() => void remove(document)}
                    disabled={busy !== null}
                  >
                    {busy === document.document_id ? "Deleting..." : "Delete document"}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => setConfirming(document.document_id)}
                >
                  Delete
                </button>
              )}
            </div>
            {previewId === document.document_id ? (
              <div className="chunk-preview">
                {previewBusy ? <p className="muted small">Loading chunks...</p> : null}
                {previewError === null ? null : (
                  <p className="error-text small" role="alert">
                    {previewError}
                  </p>
                )}
                {previewChunks.map((chunk) => (
                  <div key={chunk.ordinal} className="chunk-preview-item">
                    <span className="chunk-ordinal">#{chunk.ordinal}</span>
                    <p>{chunk.text}</p>
                  </div>
                ))}
                {!previewBusy && previewError === null && previewChunks.length === 0 ? (
                  <p className="muted small">This document has no stored chunks.</p>
                ) : null}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ConversationsSection({ tenantId }: { tenantId: string }) {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [audit, setAudit] = useState<SessionAuditRow[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    getJson<SessionAuditResponse>(
      `/sessions/audit?tenant_id=${encodeURIComponent(tenantId)}&limit=20`,
      controller.signal,
    )
      .then((payload) => {
        if (!controller.signal.aborted) {
          setAudit(payload.rows);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setAudit([]);
        }
      });
    return () => controller.abort();
  }, [tenantId, reload]);

  useEffect(() => {
    const controller = new AbortController();
    setSessions(null);
    setError(null);
    getJson<SessionsResponse>(
      `/sessions?tenant_id=${encodeURIComponent(tenantId)}&limit=50&include_deleted=true`,
      controller.signal,
    )
      .then((payload) => {
        if (!controller.signal.aborted) {
          setSessions(payload.sessions);
        }
      })
      .catch((caught) => {
        if (!isAbortError(caught)) {
          setError(errorMessage(caught));
        }
      });
    return () => controller.abort();
  }, [tenantId, reload]);

  async function restore(session: SessionSummary) {
    setBusy(session.session_id);
    setError(null);
    try {
      await restoreSession(session.session_id, tenantId);
      setReload((value) => value + 1);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  if (sessions === null && error === null) {
    return <p className="muted">Loading conversations...</p>;
  }
  if (error !== null) {
    return (
      <div className="error-box" role="alert">
        <strong>Conversations could not be loaded</strong>
        <p>{error}</p>
        <button
          type="button"
          className="button button-quiet"
          onClick={() => setReload((value) => value + 1)}
        >
          Try again
        </button>
      </div>
    );
  }
  if (sessions === null || sessions.length === 0) {
    return <p className="muted">No conversation is stored for this tenant.</p>;
  }

  return (
    <div>
      <p className="muted small">
        {sessions.length} conversation{sessions.length === 1 ? "" : "s"}, including hidden ones.
        Restoring a hidden conversation does not change its runs or traces.
      </p>
      <ul className="card-list">
        {sessions.map((session) => (
          <li key={session.session_id} className="card">
            <div className="card-head">
              <code className="card-title">{session.title}</code>
              <span className={`tag tag-${session.deleted_at === null ? "ok" : "neutral"}`}>
                {session.deleted_at === null ? "active" : "hidden"}
              </span>
            </div>
            <p className="card-text">{session.last_query}</p>
            <p className="card-meta">
              <span className="muted">
                {session.run_count} run{session.run_count === 1 ? "" : "s"}, last{" "}
                {new Date(session.last_run_at).toLocaleString()}
              </span>
            </p>
            {session.deleted_at === null ? null : (
              <div className="document-actions">
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => void restore(session)}
                  disabled={busy !== null}
                >
                  {busy === session.session_id ? "Restoring..." : "Restore"}
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      <h4 className="settings-subheading">Recent conversation actions</h4>
      {audit.length === 0 ? (
        <p className="muted small">No rename, hide or restore action is recorded.</p>
      ) : (
        <ul className="audit-list">
          {audit.map((row) => (
            <li key={row.id} className="audit-row">
              <span className="tag tag-neutral">{row.action}</span>
              <code>{row.session_id.slice(0, 12)}</code>
              <span className="muted">
                {row.actor}, {new Date(row.created_at).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UsersSection({ tenantId }: { tenantId: string }) {
  const [users, setUsers] = useState<UserSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setUsers(null);
    setError(null);
    getJson<UsersResponse>(
      `/users?tenant_id=${encodeURIComponent(tenantId)}`,
      controller.signal,
    )
      .then((payload) => {
        if (!controller.signal.aborted) {
          setUsers(payload.users);
        }
      })
      .catch((caught) => {
        if (!isAbortError(caught)) {
          setError(errorMessage(caught));
        }
      });
    return () => controller.abort();
  }, [tenantId, reload]);

  async function addUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await postJson<UserSummary>("/users", {
        tenant_id: tenantId,
        email,
        display_name: displayName,
        role,
      });
      setEmail("");
      setDisplayName("");
      setRole("member");
      setReload((value) => value + 1);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p className="muted small">
        Users belong to a tenant through a membership. This slice stores the directory and role;
        sign-in and role enforcement are the next V3.02 slice.
      </p>
      {users === null ? <p className="muted">Loading users...</p> : null}
      {error === null ? null : (
        <div className="error-box" role="alert">
          <strong>Users could not be loaded or saved</strong>
          <p>{error}</p>
        </div>
      )}
      {users === null || users.length === 0 ? (
        <p className="muted">No user belongs to this tenant yet.</p>
      ) : (
        <ul className="card-list">
          {users.map((user) => (
            <li key={user.user_id} className="card">
              <div className="card-head">
                <code className="card-title">{user.display_name}</code>
                <span className="tag tag-neutral">{user.role}</span>
              </div>
              <p className="card-text">{user.email}</p>
            </li>
          ))}
        </ul>
      )}
      <form className="settings-edit" onSubmit={addUser}>
        <h4 className="settings-subheading">Add a user to this tenant</h4>
        <label className="field">
          <span className="field-label">Email</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={busy}
            required
          />
        </label>
        <label className="field">
          <span className="field-label">Display name</span>
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            disabled={busy}
            required
          />
        </label>
        <label className="field">
          <span className="field-label">Role</span>
          <select
            value={role}
            onChange={(event) => setRole(event.target.value as "admin" | "member")}
            disabled={busy}
          >
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <div className="settings-edit-actions">
          <button type="submit" className="button button-primary" disabled={busy}>
            {busy ? "Saving..." : "Add user"}
          </button>
        </div>
      </form>
    </div>
  );
}

function TenantsSection({
  tenants,
  defaultProvider,
  agents,
}: {
  tenants: string[];
  defaultProvider: string;
  agents: SettingsAgent[];
}) {
  const [tenantId, setTenantId] = useState("");
  const [name, setName] = useState("");
  const [provider, setProvider] = useState(defaultProvider);
  const [selectedAgents, setSelectedAgents] = useState<string[]>(["direct", "docs"]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function toggleAgent(value: string) {
    setSelectedAgents((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );
  }

  async function createTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const result = await postJson<{ revision: number; note: string }>("/settings/tenants", {
        tenant_id: tenantId,
        name,
        provider,
        agents_allow: selectedAgents,
      });
      setMessage(`Saved revision ${result.revision}. ${result.note}.`);
      setTenantId("");
      setName("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p className="muted small">
        {tenants.length} tenant{tenants.length === 1 ? "" : "s"} in the control plane. Creating a
        tenant writes its database row and allowlists; restart the API to build it.
      </p>
      <ul className="card-list">
        {tenants.map((tenant) => (
          <li key={tenant} className="card">
            <div className="card-head">
              <code className="card-title">{tenant}</code>
              <span className="tag tag-neutral">tenant</span>
            </div>
          </li>
        ))}
      </ul>
      <form className="settings-edit" onSubmit={createTenant}>
        <h4 className="settings-subheading">Create a tenant</h4>
        <label className="field">
          <span className="field-label">Tenant id</span>
          <input
            value={tenantId}
            onChange={(event) => setTenantId(event.target.value)}
            placeholder="acme"
            pattern="[a-z][a-z0-9_-]{1,63}"
            disabled={busy}
            required
          />
        </label>
        <label className="field">
          <span className="field-label">Name</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={busy}
            required
          />
        </label>
        <label className="field">
          <span className="field-label">Provider</span>
          <input
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            disabled={busy}
            required
          />
        </label>
        <div className="field">
          <span className="field-label">Allowed agents</span>
          <div className="agent-toggle-list">
            {agents.map((agent) => (
              <label key={agent.name} className="agent-toggle">
                <input
                  type="checkbox"
                  checked={selectedAgents.includes(agent.name)}
                  onChange={() => toggleAgent(agent.name)}
                  disabled={busy}
                />
                {agent.name}
              </label>
            ))}
          </div>
        </div>
        <div className="settings-edit-actions">
          <button type="submit" className="button button-primary" disabled={busy}>
            {busy ? "Creating..." : "Create tenant"}
          </button>
        </div>
        {message === null ? null : <p className="muted small">{message}</p>}
        {error === null ? null : (
          <p className="error-text small" role="alert">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}

function SectionBody({
  section,
  data,
  onSaved,
}: {
  section: SectionKey;
  data: SettingsResponse;
  onSaved: () => void;
}) {
  const { platform, tenant, provider } = data;
  const [expandedServers, setExpandedServers] = useState<ReadonlySet<string>>(new Set());
  const [addingServer, setAddingServer] = useState(false);
  const [modelDraft, setModelDraft] = useState(provider.model ?? "");
  const [credentialDraft, setCredentialDraft] = useState("");
  const [credentialBusy, setCredentialBusy] = useState(false);
  const [credentialMessage, setCredentialMessage] = useState<string | null>(null);
  const [credentialError, setCredentialError] = useState<string | null>(null);
  const [agentSelection, setAgentSelection] = useState<string[]>(tenant.agents_allow);
  const [toolsAllow, setToolsAllow] = useState<string[]>(tenant.tools_allow);
  const [toolsAsk, setToolsAsk] = useState<string[]>(tenant.tools_ask);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    setModelDraft(provider.model ?? "");
  }, [provider.model]);

  useEffect(() => {
    setAgentSelection(tenant.agents_allow);
  }, [tenant.agents_allow]);

  useEffect(() => {
    setToolsAllow(tenant.tools_allow);
    setToolsAsk(tenant.tools_ask);
  }, [tenant.tools_allow, tenant.tools_ask]);

  async function saveModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const model = modelDraft.trim();
    if (!model) {
      setSaveError("Model cannot be blank.");
      return;
    }
    setSaveBusy(true);
    setSaveError(null);
    setSaveMessage(null);
    try {
      const result = await updateProviderModel(provider.name, model);
      setSaveMessage(`Saved revision ${result.revision}. ${result.note}.`);
      onSaved();
    } catch (caught) {
      setSaveError(errorMessage(caught));
    } finally {
      setSaveBusy(false);
    }
  }

  function toggleAgent(name: string) {
    setAgentSelection((current) =>
      current.includes(name)
        ? current.filter((value) => value !== name)
        : [...current, name],
    );
  }

  async function saveCredential(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (credentialDraft.trim().length < 8) {
      setCredentialError("Enter an API key of at least 8 characters.");
      return;
    }
    setCredentialBusy(true);
    setCredentialError(null);
    setCredentialMessage(null);
    try {
      const result = await saveProviderCredential(provider.name, credentialDraft.trim());
      setCredentialDraft("");
      setCredentialMessage(`Saved revision ${result.revision}. ${result.note}.`);
    } catch (caught) {
      setCredentialError(errorMessage(caught));
    } finally {
      setCredentialBusy(false);
    }
  }

  async function saveAgents(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveBusy(true);
    setSaveError(null);
    setSaveMessage(null);
    try {
      const result = await updateTenantSettings(tenant.tenant_id, {
        agents_allow: agentSelection,
      });
      setSaveMessage(`Saved revision ${result.revision}. ${result.note}.`);
      onSaved();
    } catch (caught) {
      setSaveError(errorMessage(caught));
    } finally {
      setSaveBusy(false);
    }
  }

  function toolMode(tool: SettingsTool): "allow" | "ask" | "denied" {
    if (toolsAllow.includes(tool.wire_name)) {
      return "allow";
    }
    if (toolsAsk.includes(tool.wire_name)) {
      return "ask";
    }
    return "denied";
  }

  function setToolMode(tool: SettingsTool, mode: "allow" | "ask" | "denied") {
    setToolsAllow((current) =>
      mode === "allow"
        ? [...current.filter((name) => name !== tool.wire_name), tool.wire_name]
        : current.filter((name) => name !== tool.wire_name),
    );
    setToolsAsk((current) =>
      mode === "ask"
        ? [...current.filter((name) => name !== tool.wire_name), tool.wire_name]
        : current.filter((name) => name !== tool.wire_name),
    );
  }

  async function saveTools(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveBusy(true);
    setSaveError(null);
    setSaveMessage(null);
    try {
      const result = await updateTenantSettings(tenant.tenant_id, {
        tools_allow: toolsAllow,
        tools_ask: toolsAsk,
      });
      setSaveMessage(`Saved revision ${result.revision}. ${result.note}.`);
      onSaved();
    } catch (caught) {
      setSaveError(errorMessage(caught));
    } finally {
      setSaveBusy(false);
    }
  }
  function toggleServer(name: string) {
    setExpandedServers((current) => {
      const next = new Set(current);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  }
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
        <Value label="available orchestrators">
          <Chips
            values={data.available_orchestrators}
            empty="no orchestrator was built for this provider"
          />
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

  if (section === "knowledge") {
    return <KnowledgeSection tenantId={tenant.tenant_id} />;
  }

  if (section === "conversations") {
    return <ConversationsSection tenantId={tenant.tenant_id} />;
  }

  if (section === "users") {
    return <UsersSection tenantId={tenant.tenant_id} />;
  }

  if (section === "tenants") {
    return (
      <TenantsSection
        tenants={data.tenants}
        defaultProvider={provider.name}
        agents={data.agents}
      />
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
        <form className="settings-edit" onSubmit={saveModel}>
          <h4 className="settings-subheading">Edit model</h4>
          <label className="field">
            <span className="field-label">Model name</span>
            <input
              value={modelDraft}
              onChange={(event) => setModelDraft(event.target.value)}
              disabled={saveBusy}
            />
          </label>
          <p className="muted small">
            Saves to the providers document. The base URL remains in the environment. Restart the
            API to apply.
          </p>
          <div className="settings-edit-actions">
            <button type="submit" className="button button-primary" disabled={saveBusy}>
              {saveBusy ? "Saving..." : "Save model"}
            </button>
          </div>
          {saveMessage === null ? null : <p className="muted small">{saveMessage}</p>}
          {saveError === null ? null : (
            <p className="error-text small" role="alert">
              {saveError}
            </p>
          )}
        </form>
        <form className="settings-edit" onSubmit={saveCredential}>
          <h4 className="settings-subheading">Store an API key</h4>
          <label className="field">
            <span className="field-label">API key</span>
            <input
              type="password"
              autoComplete="new-password"
              value={credentialDraft}
              onChange={(event) => setCredentialDraft(event.target.value)}
              disabled={credentialBusy}
              placeholder="stored encrypted, never shown again"
            />
          </label>
          <p className="muted small">
            Stored as a Fernet ciphertext in <code>provider_credentials</code>; the key comes from
            <code>SWITCHBOARD_SETTINGS_KEY</code> and is never returned by the API. Restart the API
            to use it.
          </p>
          <div className="settings-edit-actions">
            <button type="submit" className="button button-primary" disabled={credentialBusy}>
              {credentialBusy ? "Saving..." : "Save API key"}
            </button>
          </div>
          {credentialMessage === null ? null : (
            <p className="muted small">{credentialMessage}</p>
          )}
          {credentialError === null ? null : (
            <p className="error-text small" role="alert">
              {credentialError}
            </p>
          )}
        </form>
      </>
    );
  }

  if (section === "tools") {
    return (
      <div className="mcp-groups">
        <div className="mcp-toolbar">
          <p className="muted small">
            {data.mcp_servers.length} MCP server{data.mcp_servers.length === 1 ? "" : "s"} configured
          </p>
          <button
            type="button"
            className="button button-quiet"
            onClick={() => setAddingServer((value) => !value)}
          >
            {addingServer ? "Close" : "Add MCP server"}
          </button>
        </div>
        {addingServer ? (
          <McpServerForm onClose={() => setAddingServer(false)} />
        ) : null}
        <div className="mcp-server-list">
          {data.mcp_servers.map((server) => {
            const open = expandedServers.has(server.name);
            const tools = data.tools.filter((tool) => tool.source === server.name);
            return (
              <section key={server.name} className={`mcp-server-card${open ? " open" : ""}`}>
                <button
                  type="button"
                  className="mcp-server-card-head"
                  aria-expanded={open}
                  onClick={() => toggleServer(server.name)}
                >
                  <span className="mcp-server-card-title">
                    <code>{server.name}</code>
                    <span className="muted small">{server.adapter}</span>
                  </span>
                  <span className="mcp-server-card-meta">
                    {server.tool_count} tool{server.tool_count === 1 ? "" : "s"}
                    <ChevronDownIcon className={`mcp-server-chevron${open ? " is-open" : ""}`} />
                  </span>
                </button>
                {open ? (
                  <div className="mcp-server-card-body">
                    {tools.length === 0 ? (
                      <p className="muted small">No manifest tool is exposed by this server.</p>
                    ) : (
                      <ToolCards
                        tools={tools}
                        modeFor={toolMode}
                        onModeChange={setToolMode}
                      />
                    )}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
        <form className="settings-edit" onSubmit={saveTools}>
          <span className="muted small">
            Allow runs without approval; ask pauses a run for approval; denied hides the tool.
          </span>
          <div className="settings-edit-actions">
            <button type="submit" className="button button-primary" disabled={saveBusy}>
              {saveBusy ? "Saving..." : "Save tools"}
            </button>
          </div>
          {saveMessage === null ? null : <p className="muted small">{saveMessage}</p>}
          {saveError === null ? null : (
            <p className="error-text small" role="alert">
              {saveError}
            </p>
          )}
        </form>
      </div>
    );
  }

  if (section === "agents") {
    if (data.agents.length === 0) {
      return <p className="muted">No agent is registered in this installation.</p>;
    }
    return (
      <form className="settings-edit" onSubmit={saveAgents}>
        <ul className="card-list">
          {data.agents.map((agent) => (
            <li key={agent.name} className="card">
              <div className="card-head">
                <code className="card-title">{agent.name}</code>
                <label className="agent-toggle">
                  <input
                    type="checkbox"
                    checked={agentSelection.includes(agent.name)}
                    onChange={() => toggleAgent(agent.name)}
                    disabled={saveBusy}
                  />
                  allowed
                </label>
              </div>
              <p className="card-text">{agent.description}</p>
              <p className="card-meta">
                <span className="tag tag-neutral">
                  {agent.cacheable ? "cacheable" : "not cacheable"}
                </span>
                {agent.needs.length > 0 ? (
                  <Chips values={agent.needs} empty="" />
                ) : (
                  <span className="muted">no tools or retriever needed</span>
                )}
              </p>
            </li>
          ))}
        </ul>
        <p className="muted small">
          At least one agent must stay allowed. Changes apply after an API restart.
        </p>
        <div className="settings-edit-actions">
          <button type="submit" className="button button-primary" disabled={saveBusy}>
            {saveBusy ? "Saving..." : "Save agents"}
          </button>
        </div>
        {saveMessage === null ? null : <p className="muted small">{saveMessage}</p>}
        {saveError === null ? null : (
          <p className="error-text small" role="alert">
            {saveError}
          </p>
        )}
      </form>
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
            Runtime settings. MCP servers, the provider model, agent and tool allowlists can be
            edited here; ingested documents can be reviewed and deleted. Credentials and hot apply
            are next. Settings changes apply after an API restart. No API key or base URL is sent to
            the browser.
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
            {data !== null ? (
              <SectionBody
                section={section}
                data={data}
                onSaved={() => setReloadKey((value) => value + 1)}
              />
            ) : null}
          </div>
        </div>
      </div>
    </dialog>
  );
}
