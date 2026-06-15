import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";
import { LightboxProvider } from "./components/Lightbox";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LightboxProvider>
      <App />
    </LightboxProvider>
  </StrictMode>,
);
