import stylex from "@stylexjs/postcss-plugin";
import { stylexPlugins } from "./vendor/stylex-build.mjs";

export default {
  plugins: [
    stylex({
      include: [
        "src/**/*.{js,jsx,ts,tsx}",
        "vendor/tuja-ui/src/**/*.{js,jsx,ts,tsx}",
      ],
      useCSSLayers: true,
      babelConfig: {
        babelrc: false,
        configFile: false,
        code: false,
        parserOpts: { plugins: ["typescript", "jsx"] },
        plugins: stylexPlugins(process.env.NODE_ENV),
      },
    }),
  ],
};
