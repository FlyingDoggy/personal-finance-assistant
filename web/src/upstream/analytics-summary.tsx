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
  onTotalClick,
}: {
  heading: string;
  scope?: string;
  rangeText?: string;
  totalText: string;
  detail?: string;
  onTotalClick?: () => void;
}) {
  return (
    <section css={stack.tight} aria-label={heading}>
      <Text look="caption" tone="muted">
        {[heading, scope, rangeText].filter(Boolean).join(" · ")}
      </Text>
      {onTotalClick ? (
        <button type="button" css={[typeRole.h1, styles.total, styles.action]} onClick={onTotalClick} aria-label={`查看${heading}明细`}>
          {totalText}
        </button>
      ) : <span css={[typeRole.h1, styles.total]}>{totalText}</span>}
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
  action: {
    appearance: "none",
    borderWidth: 0,
    borderStyle: "solid",
    backgroundColor: "transparent",
    padding: 0,
    color: color.fg,
    textAlign: "start",
    cursor: "pointer",
    textDecorationLine: { default: "none", ":hover": "underline" },
    textUnderlineOffset: "0.2em",
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
