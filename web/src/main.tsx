import React from "react";
import ReactDOM from "react-dom/client";
import * as stylex from "@stylexjs/stylex";
import { root } from "@tuja/ui/primitives/root.stylex";
import App from "./App";
import { localLayout } from "./local-layout.stylex";
import { initializeAppearance } from "./appearance";
import "../vendor/global.css";
import "./styles.css";
initializeAppearance();
document.body.className =
  stylex.props(root.body, localLayout.root).className || "";
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
