// Inline SVG icons only: no icon library and no npm dependency (T3.03).
// Every icon is decorative; the control that holds it carries the accessible
// name (visible text, or text hidden with the .visually-hidden utility).

import type { ReactNode } from "react";

interface IconProps {
  className?: string;
}

function Svg({ children, className }: IconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function PlusIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M8 3v10M3 8h10" />
    </Svg>
  );
}

export function ChatIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M13.5 8.2c0 2.6-2.5 4.6-5.5 4.6-.7 0-1.4-.1-2-.3L3 14l.7-2.6C2.6 10.6 2 9.5 2 8.2 2 5.6 4.5 3.6 7.5 3.6s6 2 6 4.6Z" />
    </Svg>
  );
}

export function ListIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M6 4.5h7.5M6 8h7.5M6 11.5h7.5" />
      <circle cx="3.2" cy="4.5" r=".9" fill="currentColor" stroke="none" />
      <circle cx="3.2" cy="8" r=".9" fill="currentColor" stroke="none" />
      <circle cx="3.2" cy="11.5" r=".9" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function TraceIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M1.8 8.5h2.7l1.6-4.3 2.4 7.6 1.5-3.3h4.2" />
    </Svg>
  );
}

export function SlidersIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M2.5 5.2h11M2.5 10.8h11" />
      <circle cx="6" cy="5.2" r="1.7" fill="currentColor" />
      <circle cx="10.4" cy="10.8" r="1.7" fill="currentColor" />
    </Svg>
  );
}

export function MenuIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />
    </Svg>
  );
}

export function CloseIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </Svg>
  );
}

export function BookIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M8 4.4C6.8 3.5 5.4 3.2 3 3.4v8.4c2.4-.2 3.8.1 5 1 1.2-.9 2.6-1.2 5-1V3.4c-2.4-.2-3.8.1-5 1Z" />
      <path d="M8 4.4v8.4" />
    </Svg>
  );
}

export function ChevronDownIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M4 6.5l4 4 4-4" />
    </Svg>
  );
}

export function ArrowUpRightIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M4.5 11.5l7-7M5.5 4.5h6v6" />
    </Svg>
  );
}

export function CheckIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M3.2 8.4l3.2 3.2 6.4-7" />
    </Svg>
  );
}

export function AlertIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M8 2.4l6.1 11.2H1.9L8 2.4Z" />
      <path d="M8 6.2v3.4M8 11.4v.4" />
    </Svg>
  );
}

export function ClockIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 4.8V8l2.2 1.4" />
    </Svg>
  );
}

export function DotIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <circle cx="8" cy="8" r="3.2" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function ShieldIcon({ className }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M8 2.3l4.8 1.9v3.5c0 2.9-2 5.2-4.8 6.1-2.8-.9-4.8-3.2-4.8-6.1V4.2L8 2.3Z" />
      <path d="M6.1 8.2l1.4 1.4 2.6-2.9" />
    </Svg>
  );
}
