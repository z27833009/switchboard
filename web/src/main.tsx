import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { AuthGate } from "./components/AuthGate";
import "./styles.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("the page has no #root element");
}

createRoot(container).render(
  <StrictMode>
    <AuthGate>
      <App />
    </AuthGate>
  </StrictMode>,
);
