// Copyright (c) 2026 Qingqi Shi. MIT License.
// Source: QingqiShi/shiqingqi.com @ 8bea959a5c49dd58e449571f57ef9e5bf7506bed.
// Adapted for the local ledger API; no upstream finance calculations are included.
"use client";

import * as stylex from "@stylexjs/stylex";
import { Badge } from "@tuja/ui/components/badge";
import { cluster } from "@tuja/ui/primitives/stack.stylex";
import { typeRole } from "@tuja/ui/primitives/type.stylex";
import { color, font } from "@tuja/ui/tokens.stylex";
import type { ReactNode } from "react";

interface FieldLabelProps {
  id?: string;
  /** Marks a value the Payee's last Transaction filled in. */
  fromLastTime?: boolean;
  children: ReactNode;
}

/** The name above an editor field that is not a `TextField`, with the "from last time" mark. */
export function FieldLabel({ id, fromLastTime, children }: FieldLabelProps) {
  return (
    <span css={cluster.tight}>
      <span id={id} css={[typeRole.control, styles.label]}>
        {children}
      </span>
      {fromLastTime ? (
        <Badge intent="neutral" size="sm">
          沿用上次
        </Badge>
      ) : null}
    </span>
  );
}

const styles = stylex.create({
  label: {
    fontWeight: font.weight_6,
    color: color.fg,
  },
});
