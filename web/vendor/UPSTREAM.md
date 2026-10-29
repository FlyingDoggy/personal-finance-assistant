# Vendored Qingqi UI

Source: <https://github.com/QingqiShi/shiqingqi.com>

Pinned commit: `8bea959a5c49dd58e449571f57ef9e5bf7506bed`

This is a source copy of the unpublished `@tuja/ui` package, not a recreation of
its appearance. `web/package.json` installs it from `file:vendor/tuja-ui`.
Nothing in the build resolves to the checkout in `work/`.

## Included source

| Local path                   | Upstream path                         | Changes                                                                                                                                                                                             |
| ---------------------------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tuja-ui/src/`               | `packages/ui/src/`                    | Production source copied byte for byte, including the generated HCT palette. Test files, test setup and three Vitest-only test helpers are omitted.                                                 |
| `tuja-ui/package.json`       | `packages/ui/package.json`            | Replace pnpm catalog dependencies with exact npm versions; omit workspace development dependencies and scripts. Keep the original exports and React peer requirements.                              |
| `babel-plugins/src/`         | `packages/babel-plugins/src/`         | Copy `stylex-options`, `stylex-plugins` and `stylex-breakpoints` unchanged. Other plugins serve the website, React Compiler or i18n and are not needed here.                                        |
| `babel-plugins/package.json` | `packages/babel-plugins/package.json` | Minimal CommonJS package boundary and exports for the three included plugins.                                                                                                                       |
| `global.css`                 | `apps/web/src/app/global.css`         | Keep normalize, font definitions and document defaults. Point font URLs at local assets; omit the movie-chat-only custom property. Keep the original `@stylex` directive for build-time extraction. |
| `assets/*.woff2`             | `apps/web/public/*.woff2`             | Original Inter and IBM Plex Mono font files, unchanged.                                                                                                                                             |

`source-manifest.json` records each copied UI and Babel source file's upstream
path and SHA-256. These files were byte-compared against the pinned checkout.
Application-level Finance components copied into `web/src` are recorded in the
project's third-party notices separately.

## Vite integration

`stylex-build.mjs` is the only local adapter for the original Babel chain. It
points module resolution and breakpoint discovery at this self-contained copy.
`createRequire` keeps the CommonJS plugins outside Vite's config bundling.
Both `vite.config.ts` and `postcss.config.mjs` use the same options:

- Original `css` prop transform (`sxPropName: "css"`).
- Original media-query ordering, conditional classes and property specificity.
- Original `runtimeInjection: false`; production CSS is an external Vite asset.
- Babel 7 is used to match the existing Vite 6 React plugin and StyleX 0.19.1's
  dependency. No React Compiler is required for component correctness.

The application imports `../vendor/global.css` once. Add the classes from
`stylex.props(root.html)` and `stylex.props(root.body)` to the document elements,
where `root` comes from `@tuja/ui/primitives/root.stylex`. Components retain all
original `@tuja/ui/components/*`, token, hook and primitive subpath imports.
`tsconfig.json` includes the original `css-prop.d.ts` JSX augmentation.
`@types/dom-navigation` and `@webgpu/types` supply browser API declarations that
the application's TypeScript version does not include.

The core components work without an app-level provider. `EffectLayerProvider`
enables optional WebGPU effects; the original accessible CSS fallback remains
available without it. `ToastProvider` is needed only when using its toast hook.
Shells that use blur planes provide their own local boundary as upstream does.

`packages/props-codegen` generates design-system documentation, not runtime
component props. It is deliberately not included. No palette codegen is needed
because the committed palette output is included unchanged. No website cloud
services, Next.js, authentication, replica/database code or i18n compiler is
needed by this package.

## Licenses

- Qingqi UI, Babel plugins and copied website stylesheet: MIT, © 2026 Qingqi
  Shi. Complete notices are in `LICENSE`, `tuja-ui/LICENSE` and
  `babel-plugins/LICENSE`.
- IBM Plex Mono fonts: SIL Open Font License 1.1; original notice in
  `assets/IBM-Plex-OFL.txt`.
- Inter: SIL Open Font License 1.1; complete Inter project notice from
  <https://github.com/rsms/inter/blob/master/LICENSE.txt> is saved in
  `assets/Inter-OFL.txt`.
- npm runtime and build packages keep their licenses in their installed
  packages and exact resolutions in `web/package-lock.json`.

## Verification

On 2026-10-29, `npm install` completed with zero reported vulnerabilities. All
vendored UI source passed the application's strict TypeScript settings.
An independent production smoke entry rendered the original `TabView`,
`NavigationSplitView`, `List`, `ListRow`, `Button` and `Sheet`, and passed strict
TypeScript checking. Its production assets were served with
`script-src 'self'; style-src 'self' 'unsafe-inline'`: the purple button and Inter
font rendered, no `css` JSX attributes or inline scripts reached the DOM, and
the Sheet opened, focused its close control, closed, and returned focus to its
trigger. There were no application or CSP console errors. The smoke files and
server were removed after verification.
