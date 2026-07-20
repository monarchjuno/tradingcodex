import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "normalize.css/normalize.css";
import "@blueprintjs/core/lib/css/blueprint.css";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
