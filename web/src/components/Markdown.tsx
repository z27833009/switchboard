import { Fragment } from "react";
import type { ReactNode } from "react";

// A deliberately small Markdown renderer for model answers. It handles the
// constructs the agents actually emit (paragraphs, ordered/unordered lists,
// headings, bold/italic, inline code, fenced code and http links) and never
// injects raw HTML: every piece of text becomes a React text node, so a model
// that writes <script> is displayed, not executed. No new dependency.
const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\*[^*]+\*|_[^_]+_)/g;
const HEADING = /^(#{1,3})\s+(.+)$/;
const ORDERED = /^\s*(\d+)[.)]\s+(.+)$/;
const BULLET = /^\s*[-*+]\s+(.+)$/;

function safeHref(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = new RegExp(INLINE.source, "g");
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      parts.push(text.slice(last, match.index));
    }
    const token = match[0];
    const key = `${match.index}-${token}`;
    if (token.startsWith("**") && token.endsWith("**")) {
      parts.push(<strong key={key}>{inline(token.slice(2, -2))}</strong>);
    } else if (token.startsWith("__") && token.endsWith("__")) {
      parts.push(<strong key={key}>{inline(token.slice(2, -2))}</strong>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      parts.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("*") && token.endsWith("*")) {
      parts.push(<em key={key}>{inline(token.slice(1, -1))}</em>);
    } else if (token.startsWith("_") && token.endsWith("_")) {
      parts.push(<em key={key}>{inline(token.slice(1, -1))}</em>);
    } else {
      const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(token);
      const href = link === null ? null : safeHref(link[2]);
      if (link !== null && href !== null) {
        parts.push(
          <a key={key} href={href} target="_blank" rel="noreferrer">
            {link[1]}
          </a>,
        );
      } else {
        parts.push(token);
      }
    }
    last = match.index + token.length;
  }
  if (last < text.length) {
    parts.push(text.slice(last));
  }
  return parts;
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  let key = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    if (line.trim().startsWith("```")) {
      const language = line.trim().slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(
        <pre key={`code-${key++}`} className="markdown-code">
          <code data-language={language || undefined}>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading !== null) {
      const level = heading[1].length;
      const Tag = level === 1 ? "h2" : level === 2 ? "h3" : "h4";
      blocks.push(<Tag key={`heading-${key++}`}>{inline(heading[2])}</Tag>);
      index += 1;
      continue;
    }
    const ordered = ORDERED.exec(line);
    if (ordered !== null) {
      const start = Number(ordered[1]);
      const items: string[] = [];
      while (index < lines.length) {
        const item = ORDERED.exec(lines[index]);
        if (item === null) {
          break;
        }
        items.push(item[2]);
        index += 1;
      }
      blocks.push(
        <ol key={`ol-${key++}`} start={start}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{inline(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet !== null) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = BULLET.exec(lines[index]);
        if (item === null) {
          break;
        }
        items.push(item[1]);
        index += 1;
      }
      blocks.push(
        <ul key={`ul-${key++}`}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{inline(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length) {
      const current = lines[index];
      if (
        current.trim() === "" ||
        current.trim().startsWith("```") ||
        HEADING.test(current) ||
        ORDERED.test(current) ||
        BULLET.test(current)
      ) {
        break;
      }
      paragraph.push(current);
      index += 1;
    }
    blocks.push(
      <p key={`p-${key++}`}>
        {paragraph.map((part, partIndex) => (
          <Fragment key={partIndex}>
            {inline(part)}
            {partIndex < paragraph.length - 1 ? <br /> : null}
          </Fragment>
        ))}
      </p>,
    );
  }
  return <div className="markdown">{blocks}</div>;
}
