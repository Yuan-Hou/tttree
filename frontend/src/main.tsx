import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";
import { bootstrapAuth } from "./auth";
import { LightboxProvider } from "./components/Lightbox";
import { ToastProvider } from "./components/Toast";

// 硬要求登录:无 token 时 bootstrapAuth 已发起跳转,直接不渲染应用(避免无鉴权地闪一帧)。
if (bootstrapAuth()) {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ToastProvider>
        <LightboxProvider>
          <App />
        </LightboxProvider>
      </ToastProvider>
    </StrictMode>,
  );
}
