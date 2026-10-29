// Copyright (c) 2026 Qingqi Shi. MIT License.
// Source: QingqiShi/shiqingqi.com @ 8bea959a5c49dd58e449571f57ef9e5bf7506bed.
// Local adaptation: hash navigation, Chinese labels and local ledger capabilities.
import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { cluster, stack } from "@tuja/ui/primitives/stack.stylex";
import type { ReactNode } from "react";

interface SettingsPanelProps {
  title: string;
  description?: string;
  /** Buttons under the description, such as "Add". */
  actions?: ReactNode;
  children: ReactNode;
}

/** One Settings section: its title, a line on what it holds, its actions, and its content. */
export function SettingsPanel({
  title,
  description,
  actions,
  children,
}: SettingsPanelProps) {
  return (
    <section css={stack.group}>
      <div css={stack.item}>
        <div css={stack.tight}>
          <Heading level={2} look="h2">
            {title}
          </Heading>
          {description ? (
            <Text look="bodySmall" tone="muted">
              {description}
            </Text>
          ) : null}
        </div>
        {actions ? <div css={cluster.tight}>{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}
