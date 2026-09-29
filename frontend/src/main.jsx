import React from "react";
import { createRoot } from "react-dom/client";
import "./fonts.css";
import { ThemeProvider } from "./theme.jsx";
import FenestraApp from "./app.jsx";
import { getBridge, getBridgeSync, parseSettingsResult } from "./bridge.js";
import { FatalErrorScreen, FenestraErrorBoundary } from "./fatal-error.jsx";

const BRIDGE_READY_TIMEOUT_MS = 10_000;

/**
 * Render the application after the bridge and initial preferences are ready.
 *
 * Keeping these hooks in a separate component lets `Root` render loading and
 * error states without calling hooks conditionally.
 */
function AppWithBridge({ bridge, initialTheme, initialAccent, initialDensity, initialSettings }) {
  const [tweaks, setTweaks] = React.useState({
    theme: initialTheme,
    accent: initialAccent || "amber",
    density: initialDensity || "cozy",
    radius: 4,
    sidebarMode: "full",
  });

  React.useEffect(() => {
    const onThemeApplied = (theme) => {
      setTweaks((prev) => ({ ...prev, theme }));
    };
    bridge.theme_applied.connect(onThemeApplied);
    return () => {
      bridge.theme_applied.disconnect?.(onThemeApplied);
    };
  }, [bridge]);

  const handleSetTweaks = React.useCallback((updates) => {
    setTweaks((prev) => ({ ...prev, ...updates }));
  }, []);

  return (
    <ThemeProvider tweaks={tweaks} setTweaks={handleSetTweaks}>
      <FenestraApp bridge={bridge} initialSettings={initialSettings} />
    </ThemeProvider>
  );
}

/** Initialize the bridge and render the corresponding application state. */
function Root() {
  const [bridgeState, setBridgeState] = React.useState(null);

  React.useEffect(() => {
    // Resolve at most once from the normal path, failure path, or timeout.
    let settled = false;
    const finish = (payload) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      setBridgeState(payload);
    };
    const defaults = {
      initialTheme: globalThis.matchMedia?.("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark",
      initialAccent: "amber",
      initialDensity: "cozy",
    };
    // If the bridge callbacks never fire, render the app with defaults
    // instead of showing the loading screen forever. A cold start of the
    // WebChannel can take several seconds on a busy machine.
    const timer = setTimeout(() => {
      console.warn("[main] Bridge did not respond within 10 seconds; rendering with defaults.");
      const fallback = getBridgeSync();
      finish(
        fallback
          ? { bridge: fallback, ...defaults }
          : { error: "Fenestra could not connect to its Windows backend." },
      );
    }, BRIDGE_READY_TIMEOUT_MS);
    getBridge()
      .then((bridge) => {
        bridge.get_theme_mode((themeResult) => {
          bridge.get_settings((settingsResult) => {
            try {
              const themeResponse = JSON.parse(themeResult);
              const settingsData = parseSettingsResult(settingsResult);
              const themeData =
                themeResponse.ok && themeResponse.data
                  ? themeResponse.data
                  : { mode: "system", effective: "dark" };
              finish({
                bridge,
                initialTheme: themeData.effective || "dark",
                initialAccent: settingsData.accent || "amber",
                initialDensity: settingsData.density || "cozy",
                initialSettings: settingsData,
              });
            } catch (error) {
              console.error("[main] Failed to parse initial state:", error);
              finish({
                error: "Fenestra could not safely load its initial settings.",
              });
            }
          });
        });
      })
      .catch((error) => {
        console.error("[main] Bridge initialization failed:", error);
        const fallback = getBridgeSync();
        finish(
          fallback
            ? { bridge: fallback, ...defaults }
            : { error: "Fenestra could not connect to its Windows backend." },
        );
      });
    return () => clearTimeout(timer);
  }, []);

  if (!bridgeState) {
    return (
      <div
        role="status"
        aria-live="polite"
        style={{
          width: "100%",
          height: "100%",
          colorScheme: "light dark",
          background: "Canvas",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "CanvasText",
          fontFamily: "inherit",
          fontSize: 14,
        }}
      >
        Loading Fenestra...
      </div>
    );
  }

  if (bridgeState.error) {
    return <FatalErrorScreen message={bridgeState.error} />;
  }

  return (
    <AppWithBridge
      bridge={bridgeState.bridge}
      initialTheme={bridgeState.initialTheme}
      initialAccent={bridgeState.initialAccent}
      initialDensity={bridgeState.initialDensity}
      initialSettings={bridgeState.initialSettings}
    />
  );
}

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error('Fenestra cannot start because the "root" element is missing.');
}
const root = createRoot(rootElement);
root.render(
  <FenestraErrorBoundary>
    <Root />
  </FenestraErrorBoundary>,
);
