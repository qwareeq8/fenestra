// Main app shell: title bar, sidebar, page router, footer.

import React from "react";
import { useTokens, useTheme } from "./theme.jsx";
import { SnapPage, ExplorerPage, ShortcutsPage, GeneralPage, AboutPage } from "./pages.jsx";
import { CommandPalette } from "./panels.jsx";
import { Button, Badge, CAP_BOX, ShortcutHint } from "./primitives.jsx";
import { Icon } from "./icons.jsx";

// Minimum interval between draft writes to the bridge. Slider drags update
// the UI on every pointer move, but bridge writes are throttled to this rate
// with a trailing write so the final value is always sent.
const SAVE_THROTTLE_MS = 150;
const BRIDGE_CALL_TIMEOUT_MS = 10_000;
const SETTINGS_TRANSACTION_TOMBSTONE_TTL_MS = 5 * 60_000;
const MAX_SETTINGS_TRANSACTION_TOMBSTONES = 128;
const TRANSACTION_KEY = "__fenestraTransaction";
const SAVED_KEY = "__fenestraSaved";
const STATE_ENCODERS = {
  snapEnabled: ["enable_snap", (value) => value],
  snapKey: ["snap_key", (value) => value.toLowerCase()],
  restoreKey: ["restore_key", (value) => value.toLowerCase()],
  pressCount: ["snap_presses", (value) => value],
  interval: ["snap_interval", (value) => value],
  width: ["width_pct", (value) => value],
  height: ["height_pct", (value) => value],
  gameMode: ["game_mode_enabled", (value) => value],
  autoSize: ["ex_auto_size", (value) => value],
  launchLogin: ["run_at_startup", (value) => value],
  accent: ["accent", (value) => value],
  density: ["density", (value) => value],
  minimizeToTray: ["minimize_to_tray", (value) => value],
  themeMode: ["theme", (value) => value],
};
const PAGE_COMPONENTS = {
  snap: SnapPage,
  exp: ExplorerPage,
  keys: ShortcutsPage,
  gen: GeneralPage,
  about: AboutPage,
};

// Bridge methods and progress copy for the Explorer default-view actions.
const VIEWS_ACTIONS = {
  apply: {
    method: "apply_details_view",
    progress: "Applying Details view. File Explorer will restart...",
  },
  reset: {
    method: "reset_folder_views",
    progress: "Resetting folder views. File Explorer will restart...",
  },
};

// Human-readable copy for the raw capture_status tokens from the backend.
const CAPTURE_STATUS_COPY = {
  capturing: "Press a key, or press Esc to cancel.",
  done: "Key captured.",
  cancelled: "Capture cancelled.",
  timeout: "Key capture timed out.",
};

const STATUS_TONES = new Set(["neutral", "success", "error"]);

/**
 * Return the footer tone the backend sent with a status message.
 *
 * @param {unknown} tone - The tone argument of snap_status or views_status.
 * @returns {"error" | "success" | "neutral"} The tone, or neutral when it is unknown.
 */
export function statusTone(tone) {
  return STATUS_TONES.has(tone) ? tone : "neutral";
}

/**
 * Remember a timed-out transaction so its late settings echo is ignored.
 *
 * @param {Map<string, number>} tombstones - Transaction ids mapped to expiry times.
 * @param {string} transactionId - The transaction to remember.
 */
function rememberSettingsTransactionTombstone(tombstones, transactionId) {
  const now = Date.now();
  for (const [candidateId, expiresAt] of tombstones) {
    if (expiresAt <= now) tombstones.delete(candidateId);
  }
  tombstones.set(transactionId, now + SETTINGS_TRANSACTION_TOMBSTONE_TTL_MS);
  while (tombstones.size > MAX_SETTINGS_TRANSACTION_TOMBSTONES) {
    tombstones.delete(tombstones.keys().next().value);
  }
}

/**
 * Forget a remembered transaction and report whether it was remembered.
 *
 * @param {Map<string, number>} tombstones - Transaction ids mapped to expiry times.
 * @param {string} transactionId - The transaction to look up.
 * @returns {boolean} Whether the echo belongs to a timed-out transaction.
 */
function consumeSettingsTransactionTombstone(tombstones, transactionId) {
  const now = Date.now();
  for (const [candidateId, expiresAt] of tombstones) {
    if (expiresAt <= now) tombstones.delete(candidateId);
  }
  return tombstones.delete(transactionId);
}

/**
 * Render one native window control in the title bar.
 *
 * @param {object} props
 * @param {string} props.label - The accessible name.
 * @param {string} props.command - The window command sent to the backend.
 * @param {string} props.icon - The icon name.
 * @param {boolean} [props.danger] - Whether hover uses the Windows close color.
 * @param {object} props.bridge - The backend bridge.
 * @returns {JSX.Element} The button.
 */
function WindowControlButton({ label, command, icon, danger = false, bridge }) {
  const t = useTokens();
  const [hovered, setHovered] = React.useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      onClick={() => bridge.setWindowCommand(command, () => {})}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: 36,
        // The title bar's bottom border leaves 33 px, and a fixed 34 px
        // button would overflow its top edge.
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: hovered && danger ? "#FFFFFF" : t.textDim,
        background: hovered ? (danger ? "#E81123" : t.hover) : "transparent",
        border: "none",
        cursor: "pointer",
        fontFamily: "inherit",
        // The buttons touch the window's top and right edges, so the focus
        // ring is drawn inside them.
        outlineOffset: -2,
      }}
    >
      <Icon name={icon} size={12} />
    </button>
  );
}

/**
 * Render the frameless title bar and report its drag regions to the backend.
 *
 * @param {object} props
 * @param {() => void} props.onOpenPalette - Opens the command palette.
 * @param {object} props.bridge - The backend bridge.
 * @returns {JSX.Element} The title bar.
 */
function TitleBar({ onOpenPalette, bridge }) {
  const t = useTokens();
  const titleBarRef = React.useRef(null);
  const interactiveRef = React.useRef(null);
  const controlsRef = React.useRef(null);

  React.useLayoutEffect(() => {
    if (typeof bridge.set_hit_test_regions !== "function") return undefined;

    const reportRegions = () => {
      const titleRect = titleBarRef.current?.getBoundingClientRect();
      const interactiveRect = interactiveRef.current?.getBoundingClientRect();
      const controlsRect = controlsRef.current?.getBoundingClientRect();
      if (!titleRect || !interactiveRect || !controlsRect) return;
      const interactiveWidth = Math.ceil(interactiveRect.right - titleRect.left);
      const controlsWidth = Math.ceil(titleRect.right - controlsRect.left);
      const titleBarHeight = Math.ceil(titleRect.height);
      if (interactiveWidth <= 0 || controlsWidth <= 0 || titleBarHeight <= 0) {
        return;
      }
      bridge.set_hit_test_regions(interactiveWidth, controlsWidth, titleBarHeight, (rawResult) => {
        try {
          const result = JSON.parse(rawResult);
          if (!result?.ok) {
            console.warn(
              "[app] The backend rejected the measured title-bar regions:",
              result?.error || "Unknown error.",
            );
          }
        } catch (error) {
          console.warn("[app] The backend returned an invalid title-bar response:", error);
        }
      });
    };

    reportRegions();
    const ResizeObserverConstructor = globalThis.ResizeObserver;
    let observer = null;
    if (typeof ResizeObserverConstructor === "function") {
      observer = new ResizeObserverConstructor(reportRegions);
      observer.observe(titleBarRef.current);
      observer.observe(interactiveRef.current);
      observer.observe(controlsRef.current);
    }
    window.addEventListener("resize", reportRegions);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", reportRegions);
    };
  }, [bridge]);

  return (
    <div
      ref={titleBarRef}
      data-hit-test-region="titlebar"
      style={{
        height: 34,
        display: "flex",
        alignItems: "center",
        background: t.sidebar,
        borderBottom: `1px solid ${t.border}`,
      }}
    >
      <div
        ref={interactiveRef}
        data-hit-test-region="interactive"
        style={{
          width: 320,
          height: "100%",
          display: "flex",
          alignItems: "center",
          gap: 10,
          paddingLeft: 12,
          flexShrink: 0,
        }}
      >
        <div
          style={{
            width: 16,
            height: 16,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: t.accent,
          }}
        >
          <Icon name="mark" size={16} />
        </div>
        <div style={{ fontSize: 13, fontWeight: 700, color: t.text }}>Fenestra</div>

        <button
          aria-label="Search or jump to commands"
          onClick={onOpenPalette}
          style={{
            marginLeft: 14,
            display: "flex",
            alignItems: "center",
            gap: 8,
            height: 24,
            padding: "0 8px",
            background: t.surface,
            border: `1px solid ${t.border}`,
            borderRadius: t.radius,
            color: t.textDim,
            fontSize: 12,
            cursor: "pointer",
            fontFamily: "inherit",
            minWidth: 220,
          }}
        >
          <Icon name="search" size={11} />
          <span data-button-label="" style={{ flex: 1, textAlign: "left", ...CAP_BOX }}>
            Search or jump to...
          </span>
          <ShortcutHint keys="Ctrl K" />
        </button>
      </div>

      <div style={{ flex: 1 }} />
      <div
        ref={controlsRef}
        data-hit-test-region="controls"
        style={{
          width: 72,
          alignSelf: "stretch",
          display: "flex",
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        <WindowControlButton
          label="Minimize Fenestra"
          command="minimize"
          icon="minus"
          bridge={bridge}
        />
        <WindowControlButton
          label="Close Fenestra"
          command="close"
          icon="x"
          danger
          bridge={bridge}
        />
      </div>
    </div>
  );
}

/**
 * Render one sidebar navigation button.
 *
 * @param {object} props
 * @param {string} props.icon - The icon name.
 * @param {string} props.label - The page name.
 * @param {boolean} props.active - Whether the page is shown.
 * @param {() => void} props.onClick - Shows the page.
 * @param {string | null} [props.badge] - An optional status label.
 * @param {string} props.mode - The sidebar mode.
 * @returns {JSX.Element} The navigation button.
 */
function NavItem({ icon, label, active, onClick, badge, mode }) {
  const t = useTokens();
  const [hover, setHover] = React.useState(false);
  const iconsOnly = mode === "icons";
  return (
    <button
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={iconsOnly ? label : undefined}
      style={{
        width: "100%",
        display: "flex",
        alignItems: "center",
        gap: 10,
        height: 30,
        padding: iconsOnly ? "0" : "0 10px",
        justifyContent: iconsOnly ? "center" : "flex-start",
        borderRadius: t.radius,
        background: active ? t.surface2 : hover ? t.hover : "transparent",
        border: "none",
        boxShadow: active ? `inset 2px 0 0 ${t.accent}` : "none",
        color: active ? t.text : t.textDim,
        fontSize: 13.5,
        fontWeight: active ? 700 : 400,
        cursor: "pointer",
        textAlign: "left",
        fontFamily: "inherit",
        transition: "background .1s",
        // Adjacent items would paint over a ring drawn outside the button.
        outlineOffset: -2,
      }}
    >
      <span
        style={{
          width: 14,
          display: "flex",
          justifyContent: "center",
          color: active ? t.accent : "inherit",
        }}
      >
        <Icon name={icon} />
      </span>
      {!iconsOnly && <span style={{ flex: 1 }}>{label}</span>}
      {!iconsOnly && badge && <Badge tone="accent">{badge}</Badge>}
    </button>
  );
}

/**
 * Render the page navigation.
 *
 * @param {object} props
 * @param {string} props.nav - The current page key.
 * @param {(page: string) => void} props.setNav - Shows a page.
 * @param {object} props.app - The application state and actions.
 * @param {string} props.mode - The sidebar mode.
 * @returns {JSX.Element | null} The sidebar, or null when hidden.
 */
function Sidebar({ nav, setNav, app, mode }) {
  const t = useTokens();
  if (mode === "hidden") return null;
  const iconsOnly = mode === "icons";
  const width = iconsOnly ? 52 : 208;
  return (
    <nav
      aria-label="Settings pages"
      style={{
        width,
        background: t.sidebar,
        borderRight: `1px solid ${t.border}`,
        padding: iconsOnly ? "10px 6px" : "14px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        transition: "width .18s",
      }}
    >
      {!iconsOnly && (
        <div
          style={{
            fontSize: 12,
            color: t.textMuted,
            padding: "6px 10px 6px",
          }}
        >
          Settings
        </div>
      )}
      <NavItem
        icon="snap"
        label="Window snap"
        active={nav === "snap"}
        onClick={() => setNav("snap")}
        badge={app.saved.snapEnabled ? "On" : null}
        mode={mode}
      />
      <NavItem
        icon="folder"
        label="Explorer"
        active={nav === "exp"}
        onClick={() => setNav("exp")}
        mode={mode}
      />
      <NavItem
        icon="keyb"
        label="Shortcuts"
        active={nav === "keys"}
        onClick={() => setNav("keys")}
        mode={mode}
      />
      <div style={{ height: 10 }} />
      {!iconsOnly && (
        <div
          style={{
            fontSize: 12,
            color: t.textMuted,
            padding: "6px 10px 6px",
          }}
        >
          Application
        </div>
      )}
      <NavItem
        icon="general"
        label="General"
        active={nav === "gen"}
        onClick={() => setNav("gen")}
        mode={mode}
      />
      <NavItem
        icon="about"
        label="About"
        active={nav === "about"}
        onClick={() => setNav("about")}
        mode={mode}
      />
      <div style={{ flex: 1 }} />
      {!iconsOnly && (
        <div
          style={{
            padding: "10px 10px 4px",
            fontSize: 11,
            fontFamily: t.mono,
            color: t.textMuted,
          }}
        >
          v{__APP_VERSION__}
        </div>
      )}
    </nav>
  );
}

/**
 * Render one persistent live region for footer status messages.
 *
 * Screen readers announce changes to a live region that already exists, so
 * the container stays mounted and only its text changes.
 *
 * @param {object} props
 * @param {{message: string, tone: string} | null} props.status - The message to show, or null.
 * @param {"status" | "alert"} props.role - The live-region role.
 * @returns {JSX.Element} The live region.
 */
function StatusMessage({ status, role }) {
  const t = useTokens();
  const isError = role === "alert";
  return (
    <div
      role={role}
      title={status?.message}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 5,
        minWidth: 0,
        overflow: "hidden",
        color: isError ? t.dangerText : t.textDim,
        fontSize: 12,
      }}
    >
      {status && isError && <Icon name="x" size={12} />}
      {status && (
        <span
          style={{
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {status.message}
        </span>
      )}
    </div>
  );
}

/**
 * Render the footer with status messages and the save controls.
 *
 * @param {object} props
 * @param {boolean} props.unsaved - Whether a draft differs from the saved settings.
 * @param {boolean} props.saving - Whether a save is in progress.
 * @param {() => void} props.onSave - Commits the draft.
 * @param {() => void} props.onDiscard - Discards the draft.
 * @param {{message: string, tone: string} | null} props.status - The current message.
 * @returns {JSX.Element} The footer.
 */
function Footer({ unsaved, saving, onSave, onDiscard, status }) {
  const t = useTokens();
  return (
    <div
      style={{
        padding: "12px 24px",
        borderTop: `1px solid ${t.border}`,
        background: t.surface,
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <div style={{ display: "flex", minWidth: 0 }}>
        <StatusMessage status={status?.tone === "error" ? null : status} role="status" />
        <StatusMessage status={status?.tone === "error" ? status : null} role="alert" />
      </div>
      <div style={{ flex: 1 }} />
      {unsaved && (
        <>
          <div
            style={{
              fontSize: 12,
              color: t.textDim,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span
              style={{
                display: "inline-block",
                width: 6,
                height: 6,
                borderRadius: 3,
                background: t.warning,
              }}
            />
            Unsaved changes
          </div>
          <Button variant="secondary" onClick={onDiscard} disabled={saving}>
            Discard
          </Button>
        </>
      )}
      <Button
        variant="primary"
        onClick={onSave}
        disabled={!unsaved || saving}
        aria-keyshortcuts="Control+S"
        kbd="Ctrl S"
      >
        Save changes
      </Button>
    </div>
  );
}

/** Convert backend settings keys and defaults into frontend state. */
export function bridgeToState(settings) {
  return {
    snapEnabled: settings.enable_snap ?? true,
    snapKey: (settings.snap_key || "shift").toUpperCase(),
    restoreKey: (settings.restore_key || "ctrl").toUpperCase(),
    pressCount: settings.snap_presses ?? 3,
    interval: settings.snap_interval ?? 1050,
    width: settings.width_pct ?? 76,
    height: settings.height_pct ?? 76,
    gameMode: settings.game_mode_enabled ?? true,
    autoSize: settings.ex_auto_size ?? false,
    launchLogin: settings.run_at_startup ?? false,
    accent: settings.accent || "amber",
    density: settings.density || "cozy",
    minimizeToTray: settings.minimize_to_tray ?? true,
    themeMode: settings.theme || "system",
  };
}

/**
 * Split a backend settings payload into its draft view and its saved values.
 *
 * The draft view overlays unsaved edits on the saved settings. The backend
 * attaches the saved settings under a reserved key, which older payloads omit.
 *
 * @param {object} payload - The settings mapping from the backend.
 * @returns {{settings: object, saved: object | null}} The draft view and the saved values.
 */
export function splitSettingsPayload(payload) {
  const { [SAVED_KEY]: saved = null, ...settings } = payload ?? {};
  return { settings, saved };
}

/** Serialize a frontend state patch for the backend bridge. */
export function stateToBridge(state) {
  const payload = {};
  for (const [key, value] of Object.entries(state)) {
    const encoder = STATE_ENCODERS[key];
    if (encoder && value !== undefined) {
      payload[encoder[0]] = encoder[1](value);
    }
  }
  return JSON.stringify(payload);
}

/**
 * Render the Fenestra settings application.
 *
 * Edits are staged as a backend draft that previews live, and Save commits
 * the draft. Bridge calls are serialized so a save never overtakes a draft.
 *
 * @param {object} props
 * @param {object} props.bridge - The backend bridge or the development mock.
 * @param {object | null} [props.initialSettings] - Settings loaded before the first render.
 * @returns {JSX.Element} The application.
 */
export default function FenestraApp({ bridge, initialSettings = null }) {
  const { tweaks, setTweaks } = useTheme();
  const t = useTokens();
  const [nav, setNav] = React.useState("snap");
  const [palette, setPalette] = React.useState(false);
  const [state, setState] = React.useState(() =>
    bridgeToState(splitSettingsPayload(initialSettings).settings),
  );
  // The settings in effect, which indicators show instead of the draft.
  const [saved, setSaved] = React.useState(() => {
    const initial = splitSettingsPayload(initialSettings);
    return bridgeToState(initial.saved ?? initial.settings);
  });
  const [unsaved, setUnsaved] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [status, setStatus] = React.useState(null);
  const [captureActive, setCaptureActive] = React.useState(false);
  // The running folder-view action lives here, not in the Explorer page, so
  // it survives navigation until the backend reports completion.
  const [viewsBusy, setViewsBusy] = React.useState(null);
  const viewsBusyRef = React.useRef(null);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [settingsHydrated, setSettingsHydrated] = React.useState(initialSettings !== null);
  const stateRef = React.useRef(state);
  const dirtyRevisions = React.useRef(new Map());
  const nextRevision = React.useRef(1);
  const backendDirty = React.useRef(false);
  const backendState = React.useRef(
    initialSettings !== null ? bridgeToState(splitSettingsPayload(initialSettings).settings) : null,
  );
  const expectedSettingsSignals = React.useRef(new Map());
  const settingsTransactionTombstones = React.useRef(new Map());
  const nextTransaction = React.useRef(1);
  const operationTail = React.useRef(Promise.resolve());
  const saveInFlight = React.useRef(false);
  const saveTimer = React.useRef(null);
  const pendingSave = React.useRef({});
  const lastSaveAt = React.useRef(0);
  const mounted = React.useRef(true);
  const contentRef = React.useRef(null);

  const refreshUnsaved = React.useCallback(() => {
    if (mounted.current) {
      setUnsaved(backendDirty.current || dirtyRevisions.current.size > 0);
    }
  }, []);

  const removePendingKey = React.useCallback((key) => {
    if (!(key in pendingSave.current)) return;
    const next = { ...pendingSave.current };
    delete next[key];
    pendingSave.current = next;
  }, []);

  const applyIncomingSettings = React.useCallback(
    (payload, context = null) => {
      const { settings, saved: savedSettings } = splitSettingsPayload(payload);
      if (savedSettings && mounted.current) setSaved(bridgeToState(savedSettings));
      const incoming = bridgeToState(settings);
      const previousBackend = backendState.current;
      backendState.current = incoming;
      const next = { ...incoming };

      if (context?.type === "stage") {
        for (const [key, operationRevision] of context.revisions ?? []) {
          if (dirtyRevisions.current.get(key) !== operationRevision) {
            next[key] = stateRef.current[key];
          }
        }
      }

      for (const [key, revision] of dirtyRevisions.current) {
        const localValue = stateRef.current[key];
        if (context?.type === "stage" || context?.type === "commit") {
          // Full-state echoes from our own draft/commit calls can contain an
          // older value for a different locally edited key. Preserve every
          // local edit until the operation callback acknowledges its revision.
          next[key] = localValue;
          continue;
        }
        if (context?.type === "discard" || context?.type === "reset") {
          const operationRevision = context.revisions?.get(key);
          if (operationRevision !== undefined && revision <= operationRevision) {
            dirtyRevisions.current.delete(key);
            removePendingKey(key);
          } else {
            next[key] = localValue;
          }
          continue;
        }

        // A signal with no matching frontend operation came from an immediate
        // backend action such as Ctrl+T or a tray toggle. Only keys that
        // actually changed in the backend snapshot supersede a local edit;
        // unrelated unsent edits remain intact.
        const changedExternally =
          previousBackend !== null && !Object.is(incoming[key], previousBackend[key]);
        if (changedExternally) {
          dirtyRevisions.current.delete(key);
          removePendingKey(key);
        } else {
          next[key] = localValue;
        }
      }

      stateRef.current = next;
      if (mounted.current) setState(next);
      refreshUnsaved();
    },
    [refreshUnsaved, removePendingKey],
  );

  const enqueueOperation = React.useCallback((operation) => {
    const result = operationTail.current.then(operation, operation);
    operationTail.current = result.catch(() => undefined);
    return result;
  }, []);

  const callBridge = React.useCallback(
    (method, args = [], signalContext = null) =>
      new Promise((resolve) => {
        const transactionId = signalContext
          ? `fenestra-${Date.now()}-${nextTransaction.current++}`
          : null;
        const expectedEntry = transactionId ? { context: signalContext, cleanupTimer: null } : null;
        if (transactionId) {
          expectedSettingsSignals.current.set(transactionId, expectedEntry);
        }

        const callArgs = [...args];
        if (
          transactionId &&
          ["save_settings", "commit_draft", "discard_draft", "reset_defaults"].includes(method)
        ) {
          callArgs.push(transactionId);
        }

        const removeExpected = () => {
          if (transactionId) {
            if (expectedEntry?.cleanupTimer) {
              clearTimeout(expectedEntry.cleanupTimer);
            }
            expectedSettingsSignals.current.delete(transactionId);
          }
        };
        const armExpectedCleanup = () => {
          if (
            !transactionId ||
            expectedSettingsSignals.current.get(transactionId) !== expectedEntry
          ) {
            return;
          }
          expectedEntry.cleanupTimer = setTimeout(() => {
            if (expectedSettingsSignals.current.get(transactionId) === expectedEntry) {
              rememberSettingsTransactionTombstone(
                settingsTransactionTombstones.current,
                transactionId,
              );
              expectedSettingsSignals.current.delete(transactionId);
            }
          }, BRIDGE_CALL_TIMEOUT_MS);
        };
        let settled = false;
        let callbackTimer = null;
        const settle = (result) => {
          if (settled) return;
          settled = true;
          if (callbackTimer) clearTimeout(callbackTimer);
          if (!result.ok) removeExpected();
          else armExpectedCleanup();
          resolve(result);
        };
        const finish = (rawResult) => {
          let result;
          try {
            result = JSON.parse(rawResult);
            if (!result || typeof result !== "object") {
              throw new Error("The response is not a JSON object.");
            }
          } catch (error) {
            result = {
              ok: false,
              error: `Invalid response from ${method}: ${error.message}`,
            };
          }
          settle(result);
        };

        callbackTimer = setTimeout(() => {
          if (
            transactionId &&
            expectedSettingsSignals.current.get(transactionId) === expectedEntry
          ) {
            rememberSettingsTransactionTombstone(
              settingsTransactionTombstones.current,
              transactionId,
            );
          }
          settle({
            ok: false,
            error: `The backend method "${method}" did not respond within 10 seconds.`,
          });
        }, BRIDGE_CALL_TIMEOUT_MS);
        try {
          if (typeof bridge[method] !== "function") {
            throw new Error(`Backend method "${method}" is unavailable.`);
          }
          bridge[method](...callArgs, finish);
        } catch (error) {
          settle({
            ok: false,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }),
    [bridge],
  );

  // Footer status helper. Shows a message and clears it after timeoutMs
  // milliseconds; a timeout of 0 keeps the message until it is replaced.
  const statusTimer = React.useRef(null);
  const showStatus = React.useCallback((message, timeoutMs = 0, tone = "neutral") => {
    if (statusTimer.current) {
      clearTimeout(statusTimer.current);
      statusTimer.current = null;
    }
    if (!mounted.current) return;
    setStatus({ message: String(message), tone });
    if (tone !== "error" && timeoutMs > 0) {
      statusTimer.current = setTimeout(() => setStatus(null), timeoutMs);
    }
  }, []);
  React.useEffect(
    () => () => {
      if (statusTimer.current) clearTimeout(statusTimer.current);
    },
    [],
  );
  React.useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Load initial settings and subscribe to backend signals.
  React.useEffect(() => {
    let active = true;
    if (initialSettings === null) {
      bridge.get_settings((json) => {
        if (!active) return;
        try {
          const response = JSON.parse(json);
          if (response?.ok && response.data) {
            applyIncomingSettings(response.data, { type: "stage" });
            setSettingsHydrated(true);
          } else {
            const detail = response?.error ? ` ${response.error}` : "";
            showStatus(`Settings could not be loaded.${detail}`, 5000, "error");
          }
        } catch (error) {
          console.error("[app] Failed to parse initial settings:", error);
          showStatus("Settings could not be loaded; defaults are shown.", 5000, "error");
        }
      });
    }

    const onSettingsChanged = (json) => {
      try {
        const settings = JSON.parse(json);
        const transactionId = settings[TRANSACTION_KEY];
        delete settings[TRANSACTION_KEY];
        if (
          transactionId &&
          consumeSettingsTransactionTombstone(settingsTransactionTombstones.current, transactionId)
        ) {
          return;
        }
        const expectedEntry = transactionId
          ? expectedSettingsSignals.current.get(transactionId)
          : null;
        if (expectedEntry?.cleanupTimer) {
          clearTimeout(expectedEntry.cleanupTimer);
        }
        const context = expectedEntry?.context ?? null;
        if (transactionId) {
          expectedSettingsSignals.current.delete(transactionId);
        }
        applyIncomingSettings(settings, context ?? null);
      } catch (error) {
        console.error("[app] Failed to parse settings_changed:", error);
      }
    };

    const onDirtyChanged = (isDirty) => {
      backendDirty.current = isDirty;
      refreshUnsaved();
    };

    const onSnapStatus = (message, timeoutMs, tone) => {
      showStatus(
        message,
        typeof timeoutMs === "number" && timeoutMs >= 0 ? timeoutMs : 3000,
        statusTone(tone),
      );
    };

    const onCaptureStatus = (status) => {
      setCaptureActive(status === "capturing");
      const message = CAPTURE_STATUS_COPY[status] || status;
      const tone = status === "done" ? "success" : "neutral";
      showStatus(message, status === "capturing" ? 0 : 3000, tone);
    };

    const onViewsStatus = (message, timeoutMs, tone) => {
      viewsBusyRef.current = null;
      if (mounted.current) setViewsBusy(null);
      showStatus(
        message,
        typeof timeoutMs === "number" && timeoutMs >= 0 ? timeoutMs : 3000,
        statusTone(tone),
      );
    };

    bridge.settings_changed.connect(onSettingsChanged);
    bridge.dirty_changed.connect(onDirtyChanged);
    bridge.snap_status.connect(onSnapStatus);
    bridge.capture_status.connect(onCaptureStatus);
    bridge.views_status?.connect(onViewsStatus);

    return () => {
      active = false;
      bridge.settings_changed.disconnect?.(onSettingsChanged);
      bridge.dirty_changed.disconnect?.(onDirtyChanged);
      bridge.snap_status.disconnect?.(onSnapStatus);
      bridge.capture_status.disconnect?.(onCaptureStatus);
      bridge.views_status?.disconnect?.(onViewsStatus);
    };
  }, [applyIncomingSettings, bridge, initialSettings, refreshUnsaved, showStatus]);

  // Mirror of the latest state so `set` can compute the next local snapshot
  // without calling the bridge inside the setState updater, which must stay
  // pure.
  React.useEffect(() => {
    stateRef.current = state;
  }, [state]);

  React.useEffect(() => {
    if (!settingsHydrated) return;
    setTweaks({ accent: state.accent, density: state.density });
  }, [setTweaks, settingsHydrated, state.accent, state.density]);

  // Throttled draft writes: the first write in a burst goes out immediately,
  // later writes coalesce into one trailing write per SAVE_THROTTLE_MS
  // window, so the final value in a burst is always sent.
  const takePendingSave = React.useCallback(() => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    const queued = pendingSave.current;
    pendingSave.current = {};
    return queued;
  }, []);

  const queueDraftEntries = React.useCallback(
    (entries, reportErrors = true) => {
      if (Object.keys(entries).length === 0) return Promise.resolve({ ok: true });
      lastSaveAt.current = Date.now();
      return enqueueOperation(async () => {
        const patch = {};
        const revisions = new Map();
        for (const [key, entry] of Object.entries(entries)) {
          if (dirtyRevisions.current.get(key) === entry.revision) {
            patch[key] = entry.value;
            revisions.set(key, entry.revision);
          }
        }
        if (Object.keys(patch).length === 0) return { ok: true };
        const result = await callBridge("save_settings", [stateToBridge(patch)], {
          type: "stage",
          revisions,
        });
        if (!result.ok && reportErrors) {
          console.error("[app] save_settings failed:", result.error);
          showStatus(`Save failed: ${result.error}`, 5000, "error");
        }
        return result;
      });
    },
    [callBridge, enqueueOperation, showStatus],
  );

  const set = React.useCallback(
    (requested) => {
      const patch = Object.fromEntries(
        Object.entries(requested).filter(
          ([key, value]) => !Object.is(stateRef.current[key], value),
        ),
      );
      if (Object.keys(patch).length === 0) return;
      const next = { ...stateRef.current, ...patch };
      const revision = nextRevision.current++;
      for (const [key, value] of Object.entries(patch)) {
        dirtyRevisions.current.set(key, revision);
        pendingSave.current[key] = { value, revision };
      }
      stateRef.current = next;
      setState(next);
      refreshUnsaved();
      if (saveTimer.current) return; // A trailing write is already scheduled.
      const elapsed = Date.now() - lastSaveAt.current;
      if (elapsed >= SAVE_THROTTLE_MS) {
        void queueDraftEntries(takePendingSave());
      } else {
        saveTimer.current = setTimeout(() => {
          saveTimer.current = null;
          void queueDraftEntries(takePendingSave());
        }, SAVE_THROTTLE_MS - elapsed);
      }
    },
    [queueDraftEntries, refreshUnsaved, takePendingSave],
  );

  // Flush any queued draft write on unmount so no change is lost.
  React.useEffect(
    () => () => {
      const queued = takePendingSave();
      void queueDraftEntries(queued, false);
    },
    [queueDraftEntries, takePendingSave],
  );

  const handleSave = React.useCallback(() => {
    if (saveInFlight.current) return;
    const revisions = new Map(dirtyRevisions.current);
    // A captured key is staged by the backend alone, so its draft can be the
    // only unsaved change.
    if (revisions.size === 0 && !backendDirty.current) return;
    saveInFlight.current = true;
    takePendingSave();
    const patch = {};
    for (const key of revisions.keys()) patch[key] = stateRef.current[key];
    lastSaveAt.current = 0;
    setSaving(true);

    void enqueueOperation(async () => {
      if (revisions.size > 0) {
        const staged = await callBridge("save_settings", [stateToBridge(patch)], {
          type: "stage",
          revisions,
        });
        if (!staged.ok) {
          console.error("[app] save_settings failed:", staged.error);
          showStatus(`Save failed: ${staged.error}`, 5000, "error");
          return false;
        }
      }
      const committed = await callBridge("commit_draft", [], {
        type: "commit",
        revisions,
      });
      if (!committed.ok) {
        console.error("[app] commit_draft failed:", committed.error);
        showStatus(`Save failed: ${committed.error}`, 5000, "error");
        return false;
      }

      backendDirty.current = false;
      for (const [key, revision] of revisions) {
        if (dirtyRevisions.current.get(key) === revision) {
          dirtyRevisions.current.delete(key);
        }
      }
      refreshUnsaved();
      showStatus(
        dirtyRevisions.current.size === 0
          ? "Changes saved."
          : "Earlier changes saved; newer changes remain.",
        3000,
        "success",
      );
      return true;
    }).finally(() => {
      saveInFlight.current = false;
      if (mounted.current) setSaving(false);
    });
  }, [callBridge, enqueueOperation, refreshUnsaved, showStatus, takePendingSave]);

  const handleDiscard = () => {
    const revisions = new Map(dirtyRevisions.current);
    takePendingSave();
    lastSaveAt.current = 0;
    void enqueueOperation(async () => {
      const context = { type: "discard", revisions };
      const result = await callBridge("discard_draft", [], context);
      if (!result.ok) {
        console.error("[app] discard_draft failed:", result.error);
        showStatus(`Discard failed: ${result.error}`, 5000, "error");
        refreshUnsaved();
        return;
      }
      let data = result.data;
      if (!data) {
        const current = await callBridge("get_settings");
        if (current.ok) data = current.data;
      }
      if (data) applyIncomingSettings(data, context);
      backendDirty.current = false;
      for (const [key, revision] of revisions) {
        if (dirtyRevisions.current.get(key) === revision) {
          dirtyRevisions.current.delete(key);
        }
      }
      refreshUnsaved();
    });
  };

  const handleReset = () => {
    const revisions = new Map(dirtyRevisions.current);
    takePendingSave();
    lastSaveAt.current = 0;
    void enqueueOperation(async () => {
      const context = { type: "reset", revisions };
      const result = await callBridge("reset_defaults", [], context);
      if (!result.ok) {
        console.error("[app] reset_defaults failed:", result.error);
        showStatus(`Reset failed: ${result.error}`, 5000, "error");
        refreshUnsaved();
        return;
      }
      if (result.data) applyIncomingSettings(result.data, context);
      backendDirty.current = false;
      for (const [key, revision] of revisions) {
        if (dirtyRevisions.current.get(key) === revision) {
          dirtyRevisions.current.delete(key);
        }
      }
      refreshUnsaved();
    });
  };

  const handleTestSnap = () => {
    bridge.test_snap((result) => {
      try {
        const response = JSON.parse(result);
        // On success the backend reports through snap_status, so nothing
        // overwrites that message here.
        if (!response?.ok) {
          showStatus(
            `Test snap failed: ${response?.error || "The backend rejected the request."}`,
            5000,
            "error",
          );
        }
      } catch (error) {
        console.error("[app] Failed to parse test_snap result:", error);
        showStatus(
          "Test snap failed because the backend returned an invalid response.",
          5000,
          "error",
        );
      }
    });
  };

  const runViewsAction = React.useCallback(
    (action) => {
      if (viewsBusyRef.current !== null) return;
      const { method, progress } = VIEWS_ACTIONS[action];
      if (typeof bridge[method] !== "function") {
        showStatus("Folder view changes are not supported by this backend build.", 5000, "error");
        return;
      }
      const finishWithError = (message) => {
        viewsBusyRef.current = null;
        if (mounted.current) setViewsBusy(null);
        showStatus(message, 5000, "error");
      };
      viewsBusyRef.current = action;
      setViewsBusy(action);
      bridge[method]((rawResult) => {
        try {
          const result = JSON.parse(rawResult);
          // The callback only acknowledges the start; views_status reports
          // completion.
          if (result?.ok) showStatus(progress, 0);
          else finishWithError(result?.error || "Folder view update failed.");
        } catch (error) {
          console.error("[explorer] Failed to parse folder view result:", error);
          finishWithError("Folder view update failed.");
        }
      });
    },
    [bridge, showStatus],
  );

  const updateModalOpen = React.useCallback((isOpen) => {
    setModalOpen(Boolean(isOpen));
  }, []);

  const updateNativeShortcutGuard = React.useCallback(
    (isGuarded) => {
      bridge.set_modal_open?.(Boolean(isGuarded), (rawResult) => {
        try {
          const result = JSON.parse(rawResult);
          if (!result?.ok) {
            const detail = result?.error || "The backend rejected the request.";
            console.error("[app] set_modal_open failed:", detail);
            showStatus(`Keyboard shortcuts could not be paused: ${detail}`, 5000, "error");
          }
        } catch (error) {
          console.error("[app] Invalid set_modal_open response:", error);
          showStatus(
            "Keyboard shortcuts could not be paused because the backend returned an invalid response.",
            5000,
            "error",
          );
        }
      });
    },
    [bridge, showStatus],
  );

  React.useEffect(() => {
    updateNativeShortcutGuard(captureActive || modalOpen || palette);
  }, [captureActive, modalOpen, palette, updateNativeShortcutGuard]);

  React.useEffect(
    () => () => {
      bridge.set_modal_open?.(false, () => {});
    },
    [bridge],
  );

  const app = {
    ...state,
    saved,
    unsaved,
    set,
    onTestSnap: handleTestSnap,
    onReset: handleReset,
    bridge,
    showStatus,
    captureActive,
    saving,
    viewsBusy,
    runViewsAction,
    setCaptureActive,
    setModalOpen: updateModalOpen,
  };

  // Keep temporary surfaces mutually exclusive with key capture and modals.
  React.useEffect(() => {
    if (captureActive || modalOpen) setPalette(false);
  }, [captureActive, modalOpen]);

  // Handle application shortcuts that belong to the React surface.
  React.useEffect(() => {
    const on = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const key = e.key.toLowerCase();
      if (key === "k") {
        e.preventDefault();
        if (captureActive || modalOpen) return;
        setPalette((open) => !open);
      } else if (key === "s") {
        e.preventDefault();
        if (e.repeat || captureActive || modalOpen || palette || saving) return;
        // Blurring commits a number that is still being typed in a stepper.
        if (document.activeElement instanceof HTMLInputElement) document.activeElement.blur();
        handleSave();
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [captureActive, handleSave, modalOpen, palette, saving]);

  React.useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0;
  }, [nav]);

  const Page = PAGE_COMPONENTS[nav];

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        background: t.bg,
        color: t.text,
        fontFamily: t.font,
        fontSize: t.fontBase,
        colorScheme: t.isDark ? "dark" : "light",
        "--fenestra-focus": t.accent,
        "--fenestra-scrollbar-thumb": t.borderHi,
        "--fenestra-scrollbar-track": t.bg,
        display: "flex",
        flexDirection: "column",
        position: "relative",
      }}
    >
      <TitleBar
        onOpenPalette={() => {
          if (!captureActive && !modalOpen) setPalette(true);
        }}
        bridge={bridge}
      />
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <Sidebar nav={nav} setNav={setNav} app={app} mode={tweaks.sidebarMode} />
        {/* The scrolling page is a tab stop so the keyboard can scroll pages
            without focusable content, such as Shortcuts. Its ring is drawn
            inside because the surrounding row clips its overflow. */}
        <main
          ref={contentRef}
          tabIndex={0}
          style={{
            flex: 1,
            overflowY: "auto",
            outlineOffset: -2,
            // Scrolling a focused control into view leaves room for its ring.
            scrollPaddingBlock: 12,
            padding: `${t.cardPad + 8}px ${t.cardPad + 14}px ${t.cardPad + 8}px`,
          }}
        >
          <Page app={app} />
          <div style={{ height: 30 }} />
        </main>
      </div>
      <Footer
        unsaved={unsaved}
        saving={saving}
        onSave={handleSave}
        onDiscard={handleDiscard}
        status={status}
      />
      <CommandPalette
        open={palette && !captureActive && !modalOpen}
        onClose={() => setPalette(false)}
        app={app}
        setNav={setNav}
        onTestSnap={handleTestSnap}
        onSave={handleSave}
      />
    </div>
  );
}
