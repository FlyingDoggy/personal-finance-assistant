import {
  isValidElement,
  type ComponentProps,
  type ReactElement,
  type ReactNode,
} from "react";
import { Sheet } from "@tuja/ui/components/sheet";
import { Callout } from "@tuja/ui/components/callout";
import { TextField } from "@tuja/ui/components/text-field";
import { Textarea } from "@tuja/ui/components/textarea";
import { Select } from "@tuja/ui/components/select";
import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { FieldLabel } from "./upstream/field-label";
export type IconName =
  | "home"
  | "transactions"
  | "budget"
  | "reports"
  | "more"
  | "upload"
  | "arrow"
  | "chevron"
  | "plus"
  | "close"
  | "check"
  | "wallet"
  | "search"
  | "download"
  | "clock"
  | "refresh"
  | "leaf"
  | "edit"
  | "undo"
  | "alert";
const paths: Record<IconName, string> = {
  home: "m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z",
  transactions: "M8 4h13M8 12h13M8 20h13M3 4h.01M3 12h.01M3 20h.01",
  budget: "M4 3h16v18H4zM4 8h16M9 8v13M14 8v13M4 14h16",
  reports: "M4 3v18h17M9 16v-5M14 16V6M19 16v-8",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  chevron: "m9 5 7 7-7 7",
  plus: "M12 5v14M5 12h14",
  close: "m6 6 12 12M6 18 18 6",
  check: "m5 12 4 4L19 6",
  wallet: "M3 7h18v14H3V5a2 2 0 0 1 2-2h13v4m-2 6h5v4h-5z",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  download: "M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4",
  clock: "M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  refresh: "M20 7V3l-4 4a8 8 0 1 0 4 10M20 7h-5",
  leaf: "M20 3C9 2 2 8 6 15c5 8 15 1 14-12ZM4 21l11-12",
  edit: "m16 3 5 5L9 20l-6 1 1-6Z",
  undo: "M9 4 3 10l6 6M3 10h11a7 7 0 0 1 7 7v3",
  alert: "M12 8v5m0 4h.01M12 2 2 21h20Z",
};
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

/** Empty-state structure adapted from upstream finance/accounts/net-worth-sidebar.tsx NoAccounts. */
export function Empty({
  title,
  children,
  action,
}: {
  icon?: IconName;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div css={stack.tight}>
      <Heading level={2} look="h4">
        {title}
      </Heading>
      <Text look="bodySmall" tone="muted">
        {children}
      </Text>
      {action && <div>{action}</div>}
    </div>
  );
}
export function Notice({
  children,
  kind = "info",
}: {
  children: ReactNode;
  kind?: "error" | "info" | "success";
}) {
  return (
    <Callout intent={kind === "error" ? "danger" : kind}>{children}</Callout>
  );
}
/** Retains local form contracts while rendering upstream fields without copied chrome. */
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  if (isValidElement(children)) {
    if (children.type === "input") {
      const props = (children as ReactElement<ComponentProps<"input">>).props;
      const { size: _size, ...rest } = props;
      return <TextField {...rest} label={label} description={hint} />;
    }
    if (children.type === "select") {
      const props = (children as ReactElement<ComponentProps<"select">>).props;
      const { size: _size, ...rest } = props;
      return <Select {...rest} label={label} description={hint} />;
    }
    if (children.type === "textarea") {
      const props = (children as ReactElement<ComponentProps<"textarea">>)
        .props;
      return <Textarea {...props} label={label} description={hint} />;
    }
  }
  return (
    <label css={stack.tight}>
      <FieldLabel>{label}</FieldLabel>
      {children}
      {hint && (
        <Text as="span" look="caption" tone="muted">
          {hint}
        </Text>
      )}
    </label>
  );
}
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  return (
    <Sheet isOpen title={title} closeLabel="关闭" onClose={onClose}>
      {children}
    </Sheet>
  );
}
