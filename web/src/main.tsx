import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { APP } from "./appConfig";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { bootCardSize } from "./cardSize";
import { migrateLegacyStorage } from "./legacyStorage";
import { SourceProvider } from "./source";
import { bootTheme } from "./theme";
import "./styles.css";

if (APP.features.legacyMigration) migrateLegacyStorage();
bootTheme();
bootCardSize();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <SourceProvider>
          <App />
        </SourceProvider>
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
);
