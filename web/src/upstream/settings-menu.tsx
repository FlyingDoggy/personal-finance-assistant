// Copyright (c) 2026 Qingqi Shi. MIT License.
// Source: QingqiShi/shiqingqi.com @ 8bea959a5c49dd58e449571f57ef9e5bf7506bed.
// Local adaptation: hash navigation, Chinese labels and local ledger capabilities.
"use client";

import type { Icon } from "@phosphor-icons/react";
import { BankIcon } from "@phosphor-icons/react/dist/ssr/Bank";
import { DatabaseIcon } from "@phosphor-icons/react/dist/ssr/Database";
import { SlidersHorizontalIcon } from "@phosphor-icons/react/dist/ssr/SlidersHorizontal";
import { StackIcon } from "@phosphor-icons/react/dist/ssr/Stack";
import { StorefrontIcon } from "@phosphor-icons/react/dist/ssr/Storefront";
import { TagIcon } from "@phosphor-icons/react/dist/ssr/Tag";
import { TreeStructureIcon } from "@phosphor-icons/react/dist/ssr/TreeStructure";
import * as stylex from "@stylexjs/stylex";
import { breakpoints } from "@tuja/ui/breakpoints.stylex";
import { List } from "@tuja/ui/components/list";
import { ListRow } from "@tuja/ui/components/list-row";
import { NavGroup } from "@tuja/ui/components/nav-group";
import { NavLink } from "@tuja/ui/components/nav-link";
import { NavList } from "@tuja/ui/components/nav-list";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { color, controlSize, rhythm, space } from "@tuja/ui/tokens.stylex";
import { useId } from "react";
import {
  SETTINGS_GROUPS,
  type SettingsGroup,
  type SettingsSection,
} from "./settings-sections.ts";

const ICONS: Record<SettingsSection, Icon> = {
  accounts: BankIcon, groups: StackIcon, categories: TreeStructureIcon,
  payees: StorefrontIcon, tags: TagIcon, preferences: SlidersHorizontalIcon, data: DatabaseIcon,
};

/**
 * The Settings sections in their groups. At `lg` and wider it is a list of
 * links beside the open section, with the open one marked. Below `lg` it is
 * a page of its own: one surface of rows for each group.
 */
export function SettingsMenu({ current }: { current: SettingsSection | null }) {
  const id = useId();
  const groupLabels: Record<SettingsGroup, string> = { general: "通用", organising: "整理", device: "本设备" };
  const labels: Record<SettingsSection, string> = {
    accounts: "账户", groups: "预算项目", categories: "科目", payees: "商户与别名", tags: "标签",
    preferences: "偏好设置", data: "数据",
  };
  const menu = SETTINGS_GROUPS.map((group) => ({
    id: group.id,
    label: groupLabels[group.id],
    items: group.sections.map((section) => ({
      section,
      href: `#settings/${section}`,
      Icon: ICONS[section],
      label: labels[section],
      badge: null,
    })),
  }));

  return (
    <>
      <NavList css={styles.links}>
        {menu.map((group) => (
          <NavGroup key={group.id} label={group.label}>
            {group.items.map(({ section, href, Icon, label, badge }) => (
              <NavLink
                key={section}
                href={href}
                current={section === (current ?? "accounts")}
                leading={<Icon weight="bold" />}
              >
                {label}
                {badge}
              </NavLink>
            ))}
          </NavGroup>
        ))}
      </NavList>
      <div css={[stack.group, styles.rows]}>
        {menu.map((group) => {
          const labelId = `${id}-${group.id}`;
          return (
            <div key={group.id} css={stack.tight}>
              <Text
                as="span"
                look="overline"
                tone="muted"
                id={labelId}
                css={styles.groupLabel}
              >
                {group.label}
              </Text>
              <List aria-labelledby={labelId}>
                {group.items.map(({ section, href, Icon, label, badge }) => (
                  <ListRow
                    key={section}
                    href={href}
                    leading={
                      <span aria-hidden css={styles.rowIcon}>
                        <Icon weight="bold" />
                      </span>
                    }
                    trailing={badge}
                  >
                    {label}
                  </ListRow>
                ))}
              </List>
            </div>
          );
        })}
      </div>
    </>
  );
}

const styles = stylex.create({
  links: {
    display: { default: "none", [breakpoints.lg]: "flex" },
  },
  rows: {
    display: { default: "flex", [breakpoints.lg]: "none" },
  },
  groupLabel: {
    paddingInline: space._3,
  },
  rowIcon: {
    display: "inline-flex",
    flexShrink: 0,
    fontSize: controlSize._4,
    color: color.fgMuted,
  },
  dot: {
    display: "inline-block",
    flexShrink: 0,
    inlineSize: space._1,
    blockSize: space._1,
    marginInlineStart: rhythm.inline,
    verticalAlign: "middle",
    backgroundColor: color.fgMuted,
  },
  dotOwner: {
    backgroundColor: color.fgWarning,
  },
});
