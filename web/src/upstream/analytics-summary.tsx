// Copyright (c) 2026 Qingqi Shi. MIT License.
// Source: QingqiShi/shiqingqi.com @ 8bea959a5c49dd58e449571f57ef9e5bf7506bed.
// Adapted for the local ledger API; no upstream finance calculations are included.
import * as stylex from "@stylexjs/stylex";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { typeModifier, typeRole } from "@tuja/ui/primitives/type.stylex";
import { color, font } from "@tuja/ui/tokens.stylex";
("use client");

/** Original AnalyticsSummary presentation with preformatted local-API values. */
export function AnalyticsSummary({
  heading,
  scope,
  rangeText,
  totalText,
  detail,
}: {
  heading: string;
  scope?: string;
  rangeText?: string;
  totalText: string;
  detail?: string;
}) {
  return (
    <section css={stack.tight} aria-label={heading}>
      <Text look="caption" tone="muted">
        {[heading, scope, rangeText].filter(Boolean).join(" · ")}
      </Text>
      <span css={[typeRole.h1, styles.total]}>{totalText}</span>
      {detail && (
        <p
          css={[
            typeRole.caption,
            typeModifier.numeric,
            styles.line,
            styles.muted,
          ]}
        >
          {detail}
        </p>
      )}
    </section>
  );
}
const styles = stylex.create({
  total: {
    fontWeight: font.weight_7,
  },
  line: {
    margin: 0,
  },
  change: {
    fontWeight: font.weight_6,
    color: color.fg,
  },
  muted: {
    color: color.fgMuted,
  },
});
