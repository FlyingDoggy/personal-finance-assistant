// Copyright (c) 2026 Qingqi Shi. MIT License.
// Source: QingqiShi/shiqingqi.com @ 8bea959a5c49dd58e449571f57ef9e5bf7506bed.
// Local adaptation: hash navigation, Chinese labels and local ledger capabilities.
/** The Settings sections in their groups, in the order the menu lists them. */
export const SETTINGS_GROUPS = [
  { id: "general", sections: ["accounts"] },
  { id: "organising", sections: ["groups", "categories", "payees", "tags"] },
  { id: "device", sections: ["preferences", "data"] },
] as const;

export type SettingsGroup = (typeof SETTINGS_GROUPS)[number]["id"];

export type SettingsSection =
  (typeof SETTINGS_GROUPS)[number]["sections"][number];

/** The Settings sections, in the order the menu lists them. */
export const SETTINGS_SECTIONS: readonly SettingsSection[] =
  SETTINGS_GROUPS.flatMap((group) => group.sections);

export function isSettingsSection(
  value: string | null,
): value is SettingsSection {
  return SETTINGS_SECTIONS.some((section) => section === value);
}
