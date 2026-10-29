import * as stylex from "@stylexjs/stylex";
import { color, space } from "@tuja/ui/tokens.stylex";

/** Token aliases for layouts absent from upstream, inherited by portal contents too. */
export const localLayout = stylex.create({
  root: {
    "--app-border": color.border,
    "--app-muted": color.fgMuted,
    "--app-surface": color.bgSurface,
    "--app-sunken": color.bgSurfaceSunken,
    "--app-fg": color.fg,
    "--app-accent": color.fgAccent,
    "--app-gap": space._4,
  },
});
