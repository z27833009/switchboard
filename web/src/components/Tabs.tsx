import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useRef } from "react";

import type { TabKey } from "../routes";

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "chat", label: "Chat" },
  { key: "runs", label: "Runs" },
  { key: "traces", label: "Traces" },
];

// The tab bar is the only global navigation (T3.03). It follows the ARIA tab
// pattern from the modern-web-guidance tabs guide: one tab stop, roving
// tabindex, arrow keys with selection following focus, and an underline drawn
// with a border so the selected tab is never indicated by colour alone. The
// panel is the routed view, and its id is referenced by the tab that names it.
export function AppTabs({
  active,
  onChange,
}: {
  active: TabKey;
  onChange: (tab: TabKey) => void;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  function select(index: number) {
    const tab = TABS[index];
    if (tab === undefined) {
      return;
    }
    onChange(tab.key);
    const element = refs.current[index];
    element?.focus();
    // Keeps a partially visible tab reachable if the bar ever has to scroll.
    element?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      select((index + 1) % TABS.length);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      select((index - 1 + TABS.length) % TABS.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      select(0);
    } else if (event.key === "End") {
      event.preventDefault();
      select(TABS.length - 1);
    }
  }

  return (
    <div className="tabs" role="tablist" aria-label="Views">
      {TABS.map((tab, index) => (
        <button
          key={tab.key}
          ref={(element) => {
            refs.current[index] = element;
          }}
          type="button"
          role="tab"
          id={`app-tab-${tab.key}`}
          className="tab"
          aria-selected={active === tab.key}
          tabIndex={active === tab.key ? 0 : -1}
          onClick={() => onChange(tab.key)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
