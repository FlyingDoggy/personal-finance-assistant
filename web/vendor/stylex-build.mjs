import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const vendorRoot = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
// Keep the original CommonJS plugin outside Vite's config bundling so its
// require.resolve calls resolve relative to the vendored package itself.
const upstream = require(
  path.join(vendorRoot, "babel-plugins/src/stylex-plugins/index.js"),
);

/** The same upstream transform chain serves JavaScript and CSS extraction. */
export function stylexPlugins(nodeEnv) {
  return upstream.stylexPlugins({
    rootDir: path.resolve(vendorRoot, ".."),
    breakpointsRootDir: path.join(vendorRoot, "tuja-ui"),
    nodeEnv,
  });
}
