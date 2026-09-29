/**
 * QWebChannel bridge client for Fenestra.
 *
 * In release mode, QWebChannel is loaded via qrc:///qtwebchannel/qwebchannel.js
 * and the Python FenestraBridge QObject is available as channel.objects.bridge.
 *
 * In dev mode (Vite dev server), QWebChannel may not be available.
 * getBridge() then returns a mock bridge that keeps a saved and a draft copy
 * of the settings and answers like the backend, so the interface can be
 * exercised in an ordinary browser.
 */

let bridgeInstance = null;
let bridgePromise = null;
let devMockBridge = null;
const DEV_MODE = import.meta.env.DEV;

const MOCK_DEFAULTS = {
  snap_key: "shift",
  restore_key: "ctrl",
  enable_snap: true,
  snap_presses: 3,
  snap_interval: 1050,
  width_pct: 76,
  height_pct: 76,
  game_mode_enabled: true,
  ex_auto_size: false,
  run_at_startup: false,
  theme: "dark",
  accent: "amber",
  density: "cozy",
  minimize_to_tray: true,
};

// Query parameters that choose the mock's starting appearance, with the
// values each one accepts.
const MOCK_QUERY_CHOICES = {
  theme: ["system", "light", "dark"],
  accent: ["amber", "green", "teal", "blue", "violet"],
  density: ["compact", "cozy", "comfortable"],
};

const MOCK_TEST_SNAP_ERROR =
  "No window is eligible behind Fenestra. Open or restore another window, then try again.";
const MOCK_VIEWS_DELAY_MS = 1500;

/**
 * Read the development mock's starting theme, accent, and density.
 *
 * Unknown parameters and unsupported values are ignored, so the mock falls
 * back to its defaults for them.
 *
 * @param {string} search - A URL query string such as "?theme=light&density=compact".
 * @returns {object} The settings overrides named by the query string.
 * @example
 * mockSettingsFromQuery("?theme=light&accent=teal"); // { theme: "light", accent: "teal" }
 */
export function mockSettingsFromQuery(search) {
  const params = new URLSearchParams(search);
  const overrides = {};
  for (const [key, choices] of Object.entries(MOCK_QUERY_CHOICES)) {
    const value = params.get(key);
    if (value !== null && choices.includes(value)) overrides[key] = value;
  }
  return overrides;
}

/**
 * Create the development mock bridge.
 *
 * The mock stages edits in a draft, commits them on Save, and emits the same
 * signals as the backend, including the saved values and the tone of each
 * status message. Test snap reports that no window is eligible, as it would
 * in a browser.
 *
 * @param {object} [overrides] - Settings that replace the mock defaults.
 * @returns {object} An object with the bridge's methods and signals.
 */
export function createMockBridge(overrides = {}) {
  let saved = { ...MOCK_DEFAULTS, ...overrides };
  let draft = {};
  const listeners = new Map();

  const signal = (name) => ({
    connect: (listener) => {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(listener);
    },
    disconnect: (listener) => listeners.get(name)?.delete(listener),
  });
  const emit = (name, ...args) => {
    for (const listener of [...(listeners.get(name) ?? [])]) listener(...args);
  };
  const settingsPayload = (transactionId) => {
    const payload = { ...saved, ...draft, __fenestraSaved: { ...saved } };
    if (transactionId) payload.__fenestraTransaction = transactionId;
    return payload;
  };
  const effectiveTheme = () => {
    const mode = draft.theme ?? saved.theme;
    if (mode !== "system") return mode;
    return globalThis.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
  };
  const publish = (transactionId) => {
    emit("settings_changed", JSON.stringify(settingsPayload(transactionId)));
    emit("dirty_changed", Object.keys(draft).length > 0);
  };
  const reply = (callback, result) => callback(JSON.stringify(result));
  const finishViewsTask = (message) =>
    setTimeout(() => emit("views_status", message, 6000, "success"), MOCK_VIEWS_DELAY_MS);

  return {
    get_settings: (callback) => reply(callback, { ok: true, data: settingsPayload() }),
    save_settings: (json, transactionId, callback) => {
      const patch = JSON.parse(json);
      draft = { ...draft, ...patch };
      publish(transactionId);
      if ("theme" in patch) emit("theme_applied", effectiveTheme());
      reply(callback, { ok: true, applied: patch });
    },
    commit_draft: (transactionId, callback) => {
      const applied = draft;
      saved = { ...saved, ...draft };
      draft = {};
      publish(transactionId);
      reply(callback, { ok: true, applied });
    },
    discard_draft: (transactionId, callback) => {
      draft = {};
      publish(transactionId);
      emit("theme_applied", effectiveTheme());
      reply(callback, { ok: true, data: settingsPayload() });
    },
    reset_defaults: (transactionId, callback) => {
      saved = { ...MOCK_DEFAULTS };
      draft = {};
      publish(transactionId);
      emit("theme_applied", effectiveTheme());
      reply(callback, { ok: true, data: settingsPayload() });
    },
    test_snap: (callback) => {
      emit("snap_status", MOCK_TEST_SNAP_ERROR, 2000, "error");
      reply(callback, { ok: false, error: MOCK_TEST_SNAP_ERROR });
    },
    capture_key: (target, callback) => {
      emit("snap_status", `Press the desired ${target} key. Press Esc to cancel.`, 0, "neutral");
      emit("capture_status", "capturing");
      reply(callback, { ok: true });
    },
    cancel_capture: (callback) => {
      emit("capture_status", "cancelled");
      emit("snap_status", "Key capture cancelled.", 2000, "neutral");
      reply(callback, { ok: true });
    },
    set_modal_open: (isOpen, callback) => reply(callback, { ok: true }),
    get_theme_mode: (callback) =>
      reply(callback, {
        ok: true,
        data: { mode: draft.theme ?? saved.theme, effective: effectiveTheme() },
      }),
    setWindowCommand: (command, callback) => reply(callback, { ok: true }),
    set_hit_test_regions: (interactiveWidth, controlsWidth, titleBarHeight, callback) =>
      reply(callback, { ok: true }),
    apply_details_view: (callback) => {
      finishViewsTask("Details is now the default view for all folders.");
      reply(callback, { ok: true, data: { started: true } });
    },
    reset_folder_views: (callback) => {
      finishViewsTask("Folder views were reset to Windows defaults.");
      reply(callback, { ok: true, data: { started: true } });
    },
    settings_changed: signal("settings_changed"),
    theme_applied: signal("theme_applied"),
    snap_status: signal("snap_status"),
    capture_status: signal("capture_status"),
    dirty_changed: signal("dirty_changed"),
    views_status: signal("views_status"),
  };
}

/**
 * Return the development mock bridge, created once from the page's query string.
 *
 * Only development builds call this, so production builds gain no query
 * parameters and no mock.
 *
 * @returns {object} The development mock bridge.
 */
function getDevMockBridge() {
  devMockBridge ??= createMockBridge(mockSettingsFromQuery(globalThis.location?.search ?? ""));
  return devMockBridge;
}

/**
 * Connect to the backend once and resolve with its bridge object.
 *
 * @returns {Promise<object>} The QWebChannel bridge, or the mock in development.
 */
function initBridge() {
  if (bridgePromise) return bridgePromise;

  bridgePromise = new Promise((resolve, reject) => {
    const QWebChannelConstructor = globalThis.QWebChannel;
    const transport = globalThis.qt?.webChannelTransport;
    if (typeof QWebChannelConstructor !== "function" || !transport) {
      const message = "QWebChannel or its Qt transport is unavailable.";
      if (DEV_MODE) {
        console.warn(`[bridge] ${message} Using the development mock bridge.`);
        bridgeInstance = getDevMockBridge();
        resolve(bridgeInstance);
      } else {
        reject(new Error(message));
      }
      return;
    }

    new QWebChannelConstructor(transport, (channel) => {
      bridgeInstance = channel.objects.bridge;
      if (!bridgeInstance) {
        const message = 'QWebChannel did not expose the required "bridge" object.';
        if (!DEV_MODE) {
          reject(new Error(message));
          return;
        }
        console.warn(`[bridge] ${message} Using the development mock bridge.`);
        bridgeInstance = getDevMockBridge();
      }
      resolve(bridgeInstance);
    });
  });

  return bridgePromise;
}

/**
 * Resolve the real Qt bridge or the development-only mock bridge.
 *
 * @returns {Promise<object>} The initialized bridge object.
 */
export function getBridge() {
  return initBridge();
}

/**
 * Return the bridge synchronously if initialization has completed.
 *
 * Development builds may use the mock bridge. Production builds return
 * `null` so a broken release cannot appear functional.
 *
 * @returns {object | null} The initialized bridge, a development mock, or null.
 */
export function getBridgeSync() {
  return bridgeInstance || (DEV_MODE ? getDevMockBridge() : null);
}

/** Parse a successful settings response without substituting writeable defaults. */
export function parseSettingsResult(rawResult) {
  const response = JSON.parse(rawResult);
  const data = response?.data;
  if (response?.ok !== true || data === null || typeof data !== "object" || Array.isArray(data)) {
    throw new Error(response?.error || "The backend did not return settings data.");
  }
  return data;
}
