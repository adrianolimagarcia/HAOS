import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import "./index.css";
import App from "./App";
import { SystemActionsProvider } from "./contexts/SystemActions";
import { I18nProvider } from "./i18n";
import { exposePluginSDK } from "./plugins";
import { ThemeProvider, useTheme } from "./themes";
import { setThemeDataset } from "./themes/theme-attr";
import { HERMES_BASE_PATH } from "./lib/api";

// First paint: honor the persisted theme name before React mounts.
if (typeof window !== "undefined") {
  setThemeDataset(window.localStorage.getItem("hermes-dashboard-theme") ?? "default");
}

function ThemeAttrSync() {
  const { theme } = useTheme();
  useEffect(() => {
    setThemeDataset(theme.name);
  }, [theme.name]);
  return null;
}

// Expose the plugin SDK before rendering so plugins loaded via <script>
// can access React, components, etc. immediately.
exposePluginSDK();

createRoot(document.getElementById("root")!).render(
  <BrowserRouter basename={HERMES_BASE_PATH || undefined}>
    <I18nProvider>
      <ThemeProvider>
        <ThemeAttrSync />
        <SystemActionsProvider>
          <App />
        </SystemActionsProvider>
      </ThemeProvider>
    </I18nProvider>
  </BrowserRouter>,
);
