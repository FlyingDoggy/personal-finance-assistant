import * as stylex from "@stylexjs/stylex";
import { color } from "@tuja/ui/tokens.stylex";
import { root } from "@tuja/ui/primitives/root.stylex";
import { financeTheme } from "./upstream/finance-theme";

export type Accent = "ink" | "forest" | "navy";
export type DisplayMode = "system" | "light" | "dark";
export type Appearance = { accent: Accent; mode: DisplayMode };
const KEY = "ledger-appearance";
const forestTheme = stylex.createTheme(color, {
  bgCanvas: "light-dark(#eae9e5, #101210)",
  fgControlSelected: "light-dark(#154d3a, #b7dcc7)",
  bgControlStrongSelected: "light-dark(#154d3a, #307858)",
  bgAccent: "light-dark(#154d3a, #b7dcc7)",
  bgAccentHover: "light-dark(#23644d, #d1ebdd)",
  bgAccentSubtle: "light-dark(rgba(21,77,58,0.07), rgba(183,220,199,0.1))",
  borderAccent: "light-dark(#154d3a, #b7dcc7)",
  fgAccent: "light-dark(#154d3a, #b7dcc7)",
  fgOnAccent: "light-dark(#ffffff, #102d20)",
});
const navyTheme = stylex.createTheme(color, {
  bgCanvas: "light-dark(#eae9e5, #101112)",
  fgControlSelected: "light-dark(#243d59, #c2d4e6)",
  bgControlStrongSelected: "light-dark(#243d59, #3d6385)",
  bgAccent: "light-dark(#243d59, #c2d4e6)",
  bgAccentHover: "light-dark(#385673, #d9e5f0)",
  bgAccentSubtle: "light-dark(rgba(36,61,89,0.07), rgba(194,212,230,0.1))",
  borderAccent: "light-dark(#243d59, #c2d4e6)",
  fgAccent: "light-dark(#243d59, #c2d4e6)",
  fgOnAccent: "light-dark(#ffffff, #172637)",
});
export const accentThemes = { ink: financeTheme, forest: forestTheme, navy: navyTheme };
export function readAppearance(): Appearance {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      accent: ["ink", "forest", "navy"].includes(value?.accent) ? value.accent : "ink",
      mode: ["system", "light", "dark"].includes(value?.mode) ? value.mode : "system",
    };
  } catch { return { accent: "ink", mode: "system" }; }
}
export function applyAppearance(value: Appearance, save = true) {
  document.documentElement.className = stylex.props(root.html, accentThemes[value.accent]).className || "";
  document.documentElement.style.colorScheme = value.mode === "system" ? "light dark" : value.mode;
  document.documentElement.dataset.accent = value.accent;
  document.documentElement.dataset.displayMode = value.mode;
  if (save) {
    try { localStorage.setItem(KEY, JSON.stringify(value)); } catch { /* Current view still applies. */ }
  }
}
export function initializeAppearance() {
  applyAppearance(readAppearance(), false);
  window.addEventListener("storage", (event) => {
    if (event.key === KEY || event.key === null) applyAppearance(readAppearance(), false);
  });
}
