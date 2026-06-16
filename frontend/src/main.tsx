import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";
import { LightboxProvider } from "./components/Lightbox";
import { ToastProvider } from "./components/Toast";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <LightboxProvider>
        <App />
      </LightboxProvider>
    </ToastProvider>
  </StrictMode>,
);
