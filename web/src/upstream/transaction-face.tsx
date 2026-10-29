// Copyright (c) 2026 Qingqi Shi. MIT License.
// Source: QingqiShi/shiqingqi.com @ 8bea959a5c49dd58e449571f57ef9e5bf7506bed.
// Adapted for the local ledger API; no upstream finance calculations are included.
import * as stylex from "@stylexjs/stylex";
import { corner } from "@tuja/ui/primitives/corner.stylex";
import { align, flex } from "@tuja/ui/primitives/flex.stylex";
import { plainList, truncate } from "@tuja/ui/primitives/layout.stylex";
import { typeModifier, typeRole } from "@tuja/ui/primitives/type.stylex";
import { color, controlSize, font, rhythm } from "@tuja/ui/tokens.stylex";
import type { ReactNode } from "react";
import { transactionFaceTokens } from "./transaction-face.stylex.ts";

interface TransactionFaceProps {
  icon: ReactNode;
  title: string;
  amount: string;
  amountMuted?: boolean;
  subtitle: string;
  trailing?: ReactNode;
  children?: ReactNode;
}

export function TransactionFace({
  icon,
  title,
  amount,
  amountMuted = false,
  subtitle,
  trailing,
  children,
}: TransactionFaceProps) {
  // A row with a trailing slot is always two lines high, also when the slot is
  // empty. Thus the amounts and the trailing content of a list stay in line.
  const hasTrailing = trailing !== undefined;
  return (
    <span css={[flex.row, plainList.row, styles.face]}>
      <span css={[corner.radius_round, flex.center, styles.icon]} aria-hidden>
        {icon}
      </span>
      <span css={[flex.col, styles.main]}>
        <span css={[flex.between, styles.line]}>
          <span css={[typeRole.body, truncate.base, styles.title]}>
            {title}
          </span>
          <span
            css={[
              typeRole.body,
              typeModifier.numeric,
              styles.amount,
              amountMuted && styles.muted,
            ]}
          >
            {amount}
          </span>
        </span>
        <span css={[flex.row, align.start, styles.line]}>
          <span css={[flex.col, styles.main]}>
            {subtitle === "" ? null : (
              <span css={[typeRole.bodySmall, truncate.base, styles.muted]}>
                {subtitle}
              </span>
            )}
            {children}
          </span>
          {hasTrailing ? (
            <span
              css={[
                flex.row,
                typeRole.caption,
                typeModifier.numeric,
                styles.second,
                styles.trailing,
              ]}
            >
              {trailing}
            </span>
          ) : null}
        </span>
      </span>
    </span>
  );
}

const styles = stylex.create({
  face: {
    gap: rhythm.item,
    alignItems: "flex-start",
    paddingBlock: transactionFaceTokens.paddingBlock,
    minInlineSize: 0,
  },
  icon: {
    flexShrink: 0,
    inlineSize: transactionFaceTokens.icon,
    blockSize: transactionFaceTokens.icon,
    backgroundColor: color.bgSurfaceSunken,
    fontSize: controlSize._5,
  },
  initial: {
    color: color.fgMuted,
  },
  main: {
    flexGrow: 1,
    minInlineSize: 0,
  },
  line: {
    gap: rhythm.tight,
    minInlineSize: 0,
  },
  second: {
    minBlockSize: transactionFaceTokens.secondLine,
  },
  title: {
    fontWeight: font.weight_5,
    minInlineSize: 0,
  },
  amount: {
    flexShrink: 0,
    fontWeight: font.weight_5,
  },
  muted: {
    color: color.fgMuted,
  },
  trailing: {
    flexShrink: 0,
    color: color.fgMuted,
  },
});
