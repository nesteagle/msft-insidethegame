import { FluentProvider, webDarkTheme, webLightTheme } from "@fluentui/react-components";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ControlPanel } from "./components/ControlPanel";
import { SeasonPage } from "./components/season/SeasonPage";
import { useLocation } from "./router";
import "./app.css";
import "./i18n";

function useDarkMode(): boolean {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const [dark, setDark] = useState(query.matches);
  useEffect(() => {
    const on = (e: MediaQueryListEvent) => setDark(e.matches);
    query.addEventListener("change", on);
    return () => query.removeEventListener("change", on);
  }, [query]);
  return dark;
}

function Page() {
  const { path } = useLocation();
  if (path.startsWith("/control")) return <ControlPanel />;
  if (path.startsWith("/season")) return <SeasonPage />;
  return <App />;
}

function Root() {
  const dark = useDarkMode();
  return (
    <FluentProvider theme={dark ? webDarkTheme : webLightTheme} className="provider">
      <Page />
    </FluentProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
