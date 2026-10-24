/**
 * Creates the state source from build settings and mounts the app.
 * Rules: fail closed. A missing or unknown WEB_STATE_SOURCE means no source,
 * and every screen shows FEED LOST.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { mocks_enabled } from "./api/http";
import { App } from "./app";
import { StateSourceContext } from "./state/hooks";
import { state_source_for } from "./state/source_for";
import "./styles.css";

async function main() {
  const source_name = import.meta.env.WEB_STATE_SOURCE;
  const scenario_name = import.meta.env.WEB_SCENARIO;
  const source = state_source_for(source_name, scenario_name);

  if (source) {
    source.start();
  } else {
    console.error(
      `No state source for WEB_STATE_SOURCE="${source_name}" WEB_SCENARIO="${scenario_name}". Every screen shows FEED LOST.`,
    );
  }

  if (mocks_enabled()) {
    // If the mock worker can't start, still render: API calls then fail and say so on screen.
    try {
      const { worker } = await import("./api/mocks/browser");
      await worker.start({ onUnhandledFrame: "bypass" });
    } catch (error) {
      console.error("Browser API mocks didn't start.", error);
    }
  }

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <StateSourceContext.Provider value={source}>
        <App path={window.location.pathname} />
      </StateSourceContext.Provider>
    </StrictMode>,
  );
}

main();
