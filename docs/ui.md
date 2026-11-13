# UI design checklist (T3.03)

The visual design pass is a design task, not a product feature: the layout, palette and components below were agreed with the author before implementation, and this file is the checklist that a later session checks a change against. Every number here is a decision, not a measurement; measured bundle sizes and screenshots are recorded in `docs/spec/progress.md` under T3.03.

The shell, tokens and components live in `web/src/styles.css` and `web/src/App.tsx`. There is no UI framework, no icon library and no new npm dependency: icons are inline SVG in `web/src/components/Icons.tsx`.

## 1. Principles

1. One surface, one accent. The main area is white, the sidebar is `#F5F5F7`, and `#0071E3` is the only accent. Status colours are reserved for status.
2. Light only. No dark theme, no gradients, no glass or blur, no decorative shadows. A shadow is used only for the settings dialog and the composer.
3. Type is small and quiet. 14 px body, 12 px metadata, 20 to 24 px page titles. No display sizes inside cards.
4. The tab bar switches the current conversation's Chat / Runs / Traces views. The sidebar navigates conversations and the workspace; it never duplicates the three tabs, and switching tabs keeps the same session id.
5. No invented state. The status dot reads `GET /health`, the settings dialog reads `GET /settings`, the recent list reads `GET /sessions`, and a resumed conversation is rebuilt from stored run rows. A failed read shows an error and no values, never a placeholder that looks like data.

## 2. Design tokens

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#FFFFFF` | main surface, cards, dialogs |
| `--bg-sidebar` | `#F5F5F7` | sidebar, table head, quiet fills |
| `--text` | `#1D1D1F` | body and titles |
| `--text-secondary` | `#6E6E73` | metadata, labels, inactive tabs |
| `--border` | `#E5E5EA` | dividers, card outlines |
| `--border-strong` | `#D2D2D7` | inputs, quiet button outlines |
| `--accent` | `#0071E3` | links, primary action, selected underline |
| `--success` | `#34C759` | ok status, status dot |
| `--warning` | `#FF9F0A` | partial, clarification, incomplete trace |
| `--error` | `#FF3B30` | error status and error boxes |
| `--hover` | `#EDEDF0` | hover fill in the sidebar and menus |
| `--font` | `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, Roboto, ...` | all text |
| `--mono` | `ui-monospace, "SF Mono", "Cascadia Mono", Consolas, ...` | ids, tool names, span names |
| `--text-body` / `--text-meta` | 14 px / 12 px | body / metadata |
| `--text-title` / `--text-section` | 22 px / 16 px | page title / section title |
| `--radius-sm` / `--radius-md` / `--radius-lg` / `--radius-pill` | 8 / 12 / 16 / 999 px | controls / cards / composer and dialogs / tags |
| `--sidebar-width` / `--rail-width` | 260 px / 72 px | sidebar widths |
| `--chat-width` | 860 px | maximum width of the transcript and composer |

Soft tints (tag and box backgrounds) are derived with `color-mix()` from these tokens, so a token change carries through. Status is never conveyed by colour alone: every badge carries a glyph and a text label, and the selected tab carries an underline as well as a colour.

## 3. Layout

The shell is a two-column grid at `100dvh`: `<aside class="sidebar">` and a scrolling `<main>`-equivalent `.app-main`. `.app-main` is a column of a fixed header (title row, then tabs) and the routed panel. The panel is the only horizontally and vertically bounded area, so the sidebar can scroll its own recent list without moving the transcript.

- **Sidebar (260 px).** Brand "Switchboard" and an API status dot with its label, a full-width `+ New chat` button, a `Recent` list of conversations that scrolls independently, and a bottom profile area `Local workspace` plus the current tenant. The profile opens a menu with Settings, Switch tenant (inline tenant radios) and Docs (the API reference at `/api/docs`). There is no sign-in and no logout.
- **Icon rail (72 px).** Brand mark, new chat, up to eight recent conversations as initial-letter buttons with accessible names, a tenant switch button that shows the tenant's initial, settings and the status dot.
- **Drawer (under 1024 px).** A native `<dialog>` holding the full sidebar; opened from the menu button in the title row. The browser provides focus containment, Escape and focus return; a light backdrop click closes it, with a pointer-position fallback where `closedby` is missing.
- **Chat.** The transcript scrolls; the composer is pinned to the bottom of the view. Above the composer sits one compact run summary bar for the latest turn (plan, status, latency, tokens, cost, gate, cache). The bar's `Steps (n)` disclosure expands the live step list inline. There is no right inspector column; a citation chip opens the trace page at the cited span.
- **Runs.** The runs of the current conversation, with status and plan filters and a refresh button, then a semantic table with a row header, sticky column headers and row links. The tenant is fixed for the session; switching tenant starts a new conversation.
- **Traces.** The traces of the current conversation, one per run, with plan, status, latency, cache and trace completeness; opening one keeps the existing trace page with Timeline, Tree, Expand all, Collapse all and the plan graph.
- **Run detail and trace detail.** Their own header information plus a back link; the global tabs above stay in place and no page repeats them.
- **Settings.** A native modal `<dialog>` opened from the profile menu: a read-only notice, a vertical section sub-nav (Account, General, Model, Tools & MCP, Agents, Retrieval, Cache) and read-only cards on the right. The dialog keeps a fixed 1000 by 760 desktop size (and a fixed viewport-filling size on a phone) so changing sections does not resize the window; the content column scrolls inside it. Closing is by the close button, Escape or a backdrop click, and focus returns to the menu item that opened it.

Seven sections come from the agreed sub-nav list with `Retrieval & Cache` split into its two independent settings groups, so the dialog matches the seven-section requirement without merging two different configuration blocks.

## 4. Breakpoints

| Width | Sidebar | Main area |
| --- | --- | --- |
| `>= 1280 px` | 260 px full sidebar | content padding 32 px |
| `1024 to 1279 px` | 72 px icon rail | content padding 24 px |
| `< 1024 px` | off-canvas drawer, menu button in the title row | content padding 16 px |
| `<= 560 px` | drawer | title 19 px, subtitle hidden, single-column timeline, settings sections wrap into rows |

Chat and composer content is capped at 860 px and centred. The runs table switches to stacked cards when its own container is narrower than 760 px (`@container`), not at a viewport breakpoint, so the same table is readable in a narrow main area.

## 5. Component rules

- **Tabs.** `role="tablist"` with three `role="tab"` buttons for the current conversation, one tab stop, roving `tabindex`, ArrowLeft/ArrowRight/Home/End with selection following focus, `aria-selected`, and an underline drawn with a border on a pseudo-element. Every tab href carries the same `session` query parameter, so Chat, Runs and Traces describe one conversation. The selected tab is not distinguished by colour alone and selection does not change the tab's padding or font size.
- **Table.** A real `<table>` with a visually hidden `<caption>`, `<thead>` with `scope="col"`, a `<th scope="row">` for the query, and `data-label` on each cell for the stacked layout. The scroll container is `role="region"` with `tabindex="0"` and an accessible name, so keyboard users can scroll it.
- **Dialog.** Native `<dialog>` opened with `showModal()` only. It has `aria-labelledby`, a visible close button, `closedby="any"` as progressive enhancement, and the pointer-position fallback where closedby is unsupported. No `role="dialog"`, no manual focus trap, no `inert` toggling, no scroll lock on `html`/`body`. `dialog:modal` and `::backdrop` use `overscroll-behavior: contain`.
- **Menu.** A disclosure button with `aria-haspopup="menu"` and `aria-expanded`, a `role="menu"` of `menuitem` / `menuitemradio` items, arrow-key movement, Escape to close and return focus, and a pointerdown listener that closes on an outside click.
- **Status.** Badge tone map: `ok` -> success check, `running` -> accent clock, every warning status (`partial`, `needs_clarification`, `timeout`, `provider_error`, `auth_error`, `waiting_approval`) -> warning triangle, everything else -> neutral dot. The label is always rendered.
- **Chat.** User turns are right-aligned quiet bubbles; assistant turns are full-width with a thin divider. Streaming text occupies the answer position until `run.finished` replaces it with the final answer, status and citations. A `needs_clarification` status renders the question, not answer styling, and no citations.
- **Composer.** Enter sends and Shift+Enter adds a newline. The keydown handler calls `preventDefault()` first and returns early when `event.isComposing` or `keyCode === 229`, so an IME candidate confirmation never submits the message. The field grows with `field-sizing: content` between 1 and 10 lines; without support it keeps one row and scrolls. The send button stays the native submit path.
- **Forms.** Every control has a programmatic label (`htmlFor`/`id` or a wrapping label), and placeholders are hints, not labels. Buttons keep a 24 px minimum target; icon-only controls have a visually hidden name.
- **Motion.** No animation except a 120 ms chevron rotation; `prefers-reduced-motion: reduce` disables transitions and animations.

## 6. Accessibility checklist

- [ ] One `<h1>` per view (the conversation title or the page name), sections use `<h2>`, the settings dialog title is its own `<h2>`.
- [ ] Landmarks: sidebar `<aside>` with a label, one main region, the recent list a `<nav>` with a heading.
- [ ] Skip link before the sidebar; it focuses the `<h1>` and does not change the hash route.
- [ ] Focus is always visible (`:focus-visible` outline, 2 px accent, 2 px offset); the composer shows an outline on its container.
- [ ] No status, tag, or selected tab is conveyed by colour alone.
- [ ] Tab order follows the visual order; no positive `tabindex`; icon-only controls expose a name.
- [ ] Dialog: `aria-labelledby`, close button, Escape, backdrop click, focus containment and focus return.
- [ ] Menu: `aria-haspopup`, `aria-expanded`, `role="menu"`, arrow keys, Escape and outside click.
- [ ] Tabs: roving tabindex, arrow keys, Home/End, `aria-selected`, one `role="tabpanel"` named by the active tab.
- [ ] Table: caption, column headers, row header, `role="region"` scroller with a name.
- [ ] The runs list filters are labelled selects, and an empty result says so instead of showing an empty table.
- [ ] Every async read has a loading sentence, an error box with `role="alert"`, and an empty state; no async read renders guessed values.
- [ ] Text contrast: body `#1D1D1F` on white, secondary `#6E6E73` on white, links `#0071E3` on white.
- [ ] At 200% zoom the chat column, table cards and settings dialog stay usable (no fixed pixel heights on text containers).

## 7. Verification

Run these from the repository root and `web/`:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run --env-file .env pytest tests/integration -q
cd web; npm ci; npm run build
docker compose up -d --build
```

Manual checks, at 1440x1200, 768x1200 and 375x900 (Chrome device emulation or a resized window):

1. **375 wide, Chat.** No horizontal scrollbar on the page (`document.documentElement.scrollWidth <= window.innerWidth`); the composer stays above the bottom edge; Enter sends after typing, Shift+Enter adds a line, and an IME composition does not send. Switch to Runs or Traces and back to Chat: the URL keeps the same `session` and the transcript is unchanged.
2. **375 wide, Runs.** The table is one card per run, each cell shows its label, the row header stays visible while the card scrolls, and the page does not scroll sideways.
3. **375 wide, Settings.** Open from the profile menu (drawer). The dialog fills the viewport height, the section row wraps into two or more rows so every section stays visible, and the content column scrolls vertically without resizing the dialog when the section changes; Escape closes; focus returns to the menu item.
4. **768 wide, shell.** No static sidebar, the menu button opens the drawer, the drawer closes on Escape and on a backdrop click, and the chat column is centred below 860 px.
5. **1024 to 1279 wide.** The 72 px rail shows the brand, new chat, recent initials, tenant switch, settings and the status dot; every icon-only control has an accessible name.
6. **1440 wide, shell.** The 260 px sidebar, independent recent-list scroll, title row and tabs are all visible, and the transcript is capped at 860 px.
7. **Keyboard only.** Tab reaches the skip link, the sidebar, the tabs, the page content, the composer and the send button in that order; the tab bar is one stop and the arrow keys move between tabs; the profile menu opens with Enter and closes with Escape.
8. **Status.** Force a status (`partial`, `needs_clarification` or an error) and confirm the glyph and the label change, not only the colour.
9. **Read-only settings.** The dialog shows values from `GET /settings`; stopping the API and reopening it shows the error box and no values.

Screenshots of these checks are committed under `docs/` with the run and viewport in the name, and progress.md records them.

## 8. Known limits

- There is no authentication, so the sidebar has no sign-in, sign-out or account avatar that would pretend to be one. "Local workspace" names the workspace, and Switch tenant changes the tenant id used by every request.
- The tab bar is presented with the ARIA tab pattern because the author specified tabs with arrow keys. It switches the current conversation's Chat, Runs and Traces views, which share one session. If the views ever stop sharing one document (for example independent URLs with server-side history), they should become links instead.
- The run row stores neither the gate verdict nor the plan reason, so a resumed conversation shows `gate: not recorded` and a plan label without its reason. Live runs show both from the step events.
- The Recent list is the tenant's latest conversations grouped by `session_id`, one row per chat. Its title is the first question in that chat; the meta line shows the latest plan and time plus the number of turns. Sessionless runs are excluded because they have no conversation to resume.
- Runs and Traces are conversation-scoped. There is no all-tenant run or trace explorer in this build; switching tenant starts a new conversation, and the sidebar recent list is the way to move between stored conversations.
