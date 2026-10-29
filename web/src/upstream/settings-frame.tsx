// Copyright (c) 2026 Qingqi Shi. MIT License.
// Source: QingqiShi/shiqingqi.com @ 8bea959a5c49dd58e449571f57ef9e5bf7506bed.
// Local adaptation: hash navigation, Chinese labels and local ledger capabilities.
"use client";

import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft";
import * as stylex from "@stylexjs/stylex";
import { breakpoints } from "@tuja/ui/breakpoints.stylex";
import { AnchorButton } from "@tuja/ui/components/anchor-button";
import { Heading } from "@tuja/ui/components/heading";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import type { ReactNode } from "react";
import {
  FinanceIsland,
  FinanceIslands,
} from "./finance-page-column.tsx";
import { SettingsFinanceLogo } from "./settings-finance-logo.tsx";
import { SettingsMenu } from "./settings-menu.tsx";
import {
  type SettingsSection,
} from "./settings-sections.ts";

/**
 * Settings: the section menu beside the open section at `lg` and wider,
 * each in its own island. Below `lg` the menu is its own page and each
 * section opens on its own with a way back. Below `md` the menu page also
 * shows the signed-in Member and the Finance mark.
 */
export function SettingsFrame({ children, current }: { children: ReactNode; current: SettingsSection | null }) {
  return (
    <FinanceIslands>
      <FinanceIsland
        css={[styles.menu, current !== null && styles.menuOnSection]}
      >
        <div css={[stack.group, styles.width]}>
          <nav aria-label="设置" css={stack.group}>
            <Heading level={1} look="h3">
              设置
            </Heading>
            <SettingsMenu current={current} />
          </nav>
          {current === null ? (
            <>
              <p>本机个人账本 · 数据保存在此设备</p>
              <SettingsFinanceLogo />
            </>
          ) : null}
        </div>
      </FinanceIsland>
      {/* A new key opens each section at the top of its island. */}
      <FinanceIsland
        key={current ?? "accounts"}
        css={current === null && styles.sectionOnIndex}
      >
        <div css={[stack.item, styles.width, styles.content]}>
          {current === null ? null : (
            <div css={styles.back}>
              <AnchorButton
                href="#settings"
                look="ghost"
                size="sm"
                icon={<ArrowLeftIcon weight="bold" />}
              >
                设置
              </AnchorButton>
            </div>
          )}
          {children}
        </div>
      </FinanceIsland>
    </FinanceIslands>
  );
}

const styles = stylex.create({
  // At `lg` the menu is as wide as its longest section.
  menu: {
    flexGrow: { default: 1, [breakpoints.lg]: 0 },
    flexShrink: { default: 1, [breakpoints.lg]: 0 },
    flexBasis: { default: 0, [breakpoints.lg]: "auto" },
    inlineSize: { default: null, [breakpoints.lg]: "16rem" },
  },
  menuOnSection: {
    display: { default: "none", [breakpoints.lg]: "flex" },
  },
  width: {
    maxInlineSize: "48rem",
  },
  content: {
    minInlineSize: 0,
  },
  sectionOnIndex: {
    display: { default: "none", [breakpoints.lg]: "flex" },
  },
  back: {
    display: { default: "block", [breakpoints.lg]: "none" },
  },
});
