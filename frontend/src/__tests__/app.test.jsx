import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act, within } from "@testing-library/react";
import { ThemeProvider } from "../theme.jsx";
import FenestraApp, { bridgeToState, stateToBridge } from "../app.jsx";

describe("bridgeToState", () => {
  it("Maps Python snake_case keys to React camelCase.", () => {
    const result = bridgeToState({
      enable_snap: true,
      snap_key: "shift",
      restore_key: "ctrl",
      snap_presses: 3,
      snap_interval: 1050,
      width_pct: 76,
      height_pct: 76,
      game_mode_enabled: true,
      ex_auto_size: false,
      run_at_startup: false,
    });
    expect(result.snapEnabled).toBe(true);
    expect(result.snapKey).toBe("SHIFT");
    expect(result.restoreKey).toBe("CTRL");
    expect(result.pressCount).toBe(3);
    expect(result.interval).toBe(1050);
    expect(result.width).toBe(76);
    expect(result.height).toBe(76);
    expect(result.gameMode).toBe(true);
    expect(result.autoSize).toBe(false);
    expect(result.launchLogin).toBe(false);
  });

  it("Uppercases snap_key and restore_key.", () => {
    const result = bridgeToState({ snap_key: "ctrl", restore_key: "alt" });
    expect(result.snapKey).toBe("CTRL");
    expect(result.restoreKey).toBe("ALT");
  });

  it("Applies default values through nullish coalescing.", () => {
    const result = bridgeToState({});
    expect(result.snapEnabled).toBe(true);
    expect(result.snapKey).toBe("SHIFT");
    expect(result.restoreKey).toBe("CTRL");
    expect(result.pressCount).toBe(3);
    expect(result.interval).toBe(1050);
    expect(result.width).toBe(76);
    expect(result.height).toBe(76);
    expect(result.gameMode).toBe(true);
    expect(result.autoSize).toBe(false);
    expect(result.launchLogin).toBe(false);
  });

  it("Preserves explicit false values.", () => {
    const result = bridgeToState({
      enable_snap: false,
      game_mode_enabled: false,
    });
    expect(result.snapEnabled).toBe(false);
    expect(result.gameMode).toBe(false);
  });
});

describe("stateToBridge", () => {
  it("Maps React camelCase keys back to Python snake_case JSON.", () => {
    const json = stateToBridge({
      snapEnabled: true,
      snapKey: "SHIFT",
      restoreKey: "CTRL",
      pressCount: 3,
      interval: 1050,
      width: 76,
      height: 76,
      gameMode: true,
      autoSize: false,
      launchLogin: false,
    });
    const parsed = JSON.parse(json);
    expect(parsed.enable_snap).toBe(true);
    expect(parsed.snap_key).toBe("shift");
    expect(parsed.restore_key).toBe("ctrl");
    expect(parsed.snap_presses).toBe(3);
    expect(parsed.snap_interval).toBe(1050);
    expect(parsed.width_pct).toBe(76);
    expect(parsed.height_pct).toBe(76);
    expect(parsed.game_mode_enabled).toBe(true);
    expect(parsed.ex_auto_size).toBe(false);
    expect(parsed.run_at_startup).toBe(false);
  });

  it("Lowercases key names for the Python bridge.", () => {
    const json = stateToBridge({ snapKey: "SHIFT", restoreKey: "ALT" });
    const parsed = JSON.parse(json);
    expect(parsed.snap_key).toBe("shift");
    expect(parsed.restore_key).toBe("alt");
  });

  it("Returns a valid JSON string.", () => {
    const json = stateToBridge({
      snapEnabled: true,
      snapKey: "SHIFT",
      restoreKey: "CTRL",
      pressCount: 3,
      interval: 1050,
      width: 76,
      height: 76,
      gameMode: true,
      autoSize: false,
      launchLogin: false,
    });
    expect(typeof json).toBe("string");
    expect(() => JSON.parse(json)).not.toThrow();
  });

  it("Serializes only fields present in a draft patch.", () => {
    expect(JSON.parse(stateToBridge({ width: 81 }))).toEqual({ width_pct: 81 });
    expect(JSON.parse(stateToBridge({ gameMode: false }))).toEqual({
      game_mode_enabled: false,
    });
  });
});

// A minimal bridge mock for full-app renders. Callbacks resolve synchronously
// so tests can assert call ordering without awaiting the event loop.
function makeBridge() {
  const signal = () => ({ connect: vi.fn(), disconnect: vi.fn() });
  return {
    get_settings: vi.fn((cb) => cb(JSON.stringify({ ok: true, data: {} }))),
    settings_changed: signal(),
    dirty_changed: signal(),
    snap_status: signal(),
    capture_status: signal(),
    views_status: signal(),
    save_settings: vi.fn((json, transactionId, cb) => cb(JSON.stringify({ ok: true }))),
    commit_draft: vi.fn((transactionId, cb) => cb(JSON.stringify({ ok: true }))),
    discard_draft: vi.fn((transactionId, cb) => cb(JSON.stringify({ ok: true }))),
    reset_defaults: vi.fn((transactionId, cb) => cb(JSON.stringify({ ok: true, data: {} }))),
    test_snap: vi.fn(),
    capture_key: vi.fn((target, cb) => cb(JSON.stringify({ ok: true }))),
    cancel_capture: vi.fn((cb) => cb(JSON.stringify({ ok: true }))),
    set_modal_open: vi.fn((isOpen, cb) => cb(JSON.stringify({ ok: true }))),
    set_hit_test_regions: vi.fn((interactiveWidth, controlsWidth, titleBarHeight, cb) =>
      cb(JSON.stringify({ ok: true })),
    ),
    setWindowCommand: vi.fn(),
    apply_details_view: vi.fn((cb) => cb(JSON.stringify({ ok: true, data: { started: true } }))),
    reset_folder_views: vi.fn((cb) => cb(JSON.stringify({ ok: true, data: { started: true } }))),
  };
}

function renderApp(bridge) {
  const tweaks = {
    theme: "dark",
    accent: "amber",
    density: "cozy",
    radius: 4,
    sidebarMode: "full",
  };
  return render(
    <ThemeProvider tweaks={tweaks} setTweaks={vi.fn()}>
      <FenestraApp bridge={bridge} />
    </ThemeProvider>,
  );
}

async function flushOperations() {
  await act(async () => {
    for (let index = 0; index < 8; index += 1) await Promise.resolve();
  });
}

describe("throttled draft writes versus save, discard, and reset", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("Acknowledges the latest draft snapshot before committing.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    renderApp(bridge);
    // The second switch on the default Window snap page is Game mode.
    const toggle = screen.getAllByRole("switch")[1];
    fireEvent.click(toggle);
    await flushOperations();
    expect(bridge.save_settings).toHaveBeenCalledTimes(1);
    // A second change inside the throttle window only queues a trailing write.
    fireEvent.click(toggle);
    expect(bridge.save_settings).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await flushOperations();
    // A complete snapshot goes out and is acknowledged before the commit.
    expect(bridge.save_settings).toHaveBeenCalledTimes(2);
    expect(JSON.parse(bridge.save_settings.mock.calls[0][0])).toEqual({
      game_mode_enabled: false,
    });
    expect(bridge.commit_draft).toHaveBeenCalledTimes(1);
    expect(bridge.save_settings.mock.invocationCallOrder[1]).toBeLessThan(
      bridge.commit_draft.mock.invocationCallOrder[0],
    );
    // No trailing write fires after the save.
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(bridge.save_settings).toHaveBeenCalledTimes(2);
  });

  it("Preserves newer local edits when an older settings echo arrives.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    renderApp(bridge);
    const [snapToggle, gameToggle] = screen.getAllByRole("switch");

    fireEvent.click(gameToggle);
    await flushOperations();
    fireEvent.click(gameToggle);
    const onSettingsChanged = bridge.settings_changed.connect.mock.calls[0][0];
    const transactionId = bridge.save_settings.mock.calls[0][1];
    act(() =>
      onSettingsChanged(
        JSON.stringify({
          enable_snap: true,
          game_mode_enabled: false,
          __fenestraTransaction: transactionId,
        }),
      ),
    );
    fireEvent.click(snapToggle);
    act(() => vi.advanceTimersByTime(200));
    await flushOperations();

    const trailing = JSON.parse(bridge.save_settings.mock.calls.at(-1)[0]);
    expect(trailing).toEqual({
      enable_snap: false,
      game_mode_enabled: true,
    });
  });

  it("Sends the first new edit immediately after a successful commit.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    renderApp(bridge);
    const toggle = screen.getAllByRole("switch")[1];

    fireEvent.click(toggle);
    await flushOperations();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await flushOperations();
    fireEvent.click(toggle);
    await flushOperations();

    expect(bridge.save_settings).toHaveBeenCalledTimes(3);
  });

  it("Cancels the queued trailing write on discard.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    renderApp(bridge);
    const toggle = screen.getAllByRole("switch")[1];
    fireEvent.click(toggle);
    await flushOperations();
    fireEvent.click(toggle);
    expect(bridge.save_settings).toHaveBeenCalledTimes(1);
    // Mark the draft dirty so the Discard button appears in the footer.
    const onDirty = bridge.dirty_changed.connect.mock.calls[0][0];
    act(() => onDirty(true));
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await flushOperations();
    expect(bridge.discard_draft).toHaveBeenCalledTimes(1);
    // The queued write is dropped, so nothing re-dirties the settings.
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(bridge.save_settings).toHaveBeenCalledTimes(1);
  });

  it("Cancels the queued trailing write on reset.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    renderApp(bridge);
    const toggle = screen.getAllByRole("switch")[1];
    fireEvent.click(toggle);
    await flushOperations();
    fireEvent.click(toggle);
    expect(bridge.save_settings).toHaveBeenCalledTimes(1);
    // Navigate to General and confirm the reset dialog.
    fireEvent.click(screen.getByRole("button", { name: "General" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reset" }));
    await flushOperations();
    expect(bridge.reset_defaults).toHaveBeenCalledTimes(1);
    // The queued write is dropped, so it cannot overwrite the defaults.
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(bridge.save_settings).toHaveBeenCalledTimes(1);
  });

  it("Keeps dirty state when the final draft write fails.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    bridge.save_settings
      .mockImplementationOnce((json, transactionId, cb) => cb(JSON.stringify({ ok: true })))
      .mockImplementationOnce((json, transactionId, cb) =>
        cb(JSON.stringify({ ok: false, error: "write rejected" })),
      );
    renderApp(bridge);

    fireEvent.click(screen.getAllByRole("switch")[1]);
    await flushOperations();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await flushOperations();

    expect(bridge.commit_draft).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Save failed: write rejected");
    expect(alert).toHaveAttribute("title", "Save failed: write rejected");
    act(() => vi.advanceTimersByTime(60_000));
    expect(alert).toBeInTheDocument();
  });

  it("Recovers the settings queue when a bridge callback is lost.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    bridge.save_settings
      .mockImplementationOnce(() => {})
      .mockImplementation((json, transactionId, callback) =>
        callback(JSON.stringify({ ok: true })),
      );
    renderApp(bridge);

    fireEvent.click(screen.getAllByRole("switch")[1]);
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(bridge.commit_draft).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await flushOperations();

    expect(bridge.save_settings).toHaveBeenCalledTimes(2);
    expect(bridge.commit_draft).toHaveBeenCalledTimes(1);
  });

  it("Prevents duplicate explicit saves in the same event turn.", async () => {
    const bridge = makeBridge();
    renderApp(bridge);
    fireEvent.click(screen.getAllByRole("switch")[1]);
    await flushOperations();

    act(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true }),
      );
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true }),
      );
    });
    await flushOperations();

    expect(bridge.commit_draft).toHaveBeenCalledTimes(1);
  });

  it("Keeps save and discard unavailable while a commit is pending.", async () => {
    const bridge = makeBridge();
    let finishCommit;
    bridge.commit_draft.mockImplementation((transactionId, callback) => {
      finishCommit = callback;
    });
    renderApp(bridge);
    fireEvent.click(screen.getAllByRole("switch")[1]);
    await flushOperations();

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await flushOperations();

    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const palette = screen.getByRole("dialog", { name: "Command palette" });
    expect(within(palette).queryByRole("option", { name: "Save changes" })).not.toBeInTheDocument();

    act(() => finishCommit(JSON.stringify({ ok: true })));
    await flushOperations();
  });

  it("Ignores a late tagged echo after its bridge callback times out.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    bridge.save_settings
      .mockImplementationOnce(() => {})
      .mockImplementation((json, transactionId, callback) =>
        callback(JSON.stringify({ ok: true })),
      );
    renderApp(bridge);
    const gameMode = screen.getAllByRole("switch")[1];

    fireEvent.click(gameMode);
    await flushOperations();
    const timedOutTransaction = bridge.save_settings.mock.calls[0][1];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await flushOperations();
    fireEvent.click(gameMode);
    await flushOperations();

    const onSettingsChanged = bridge.settings_changed.connect.mock.calls[0][0];
    act(() =>
      onSettingsChanged(
        JSON.stringify({
          game_mode_enabled: false,
          __fenestraTransaction: timedOutTransaction,
        }),
      ),
    );

    expect(gameMode).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("Ignores a late tagged echo after successful-callback cleanup.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    renderApp(bridge);
    const gameMode = screen.getAllByRole("switch")[1];

    fireEvent.click(gameMode);
    await flushOperations();
    const cleanedTransaction = bridge.save_settings.mock.calls[0][1];
    fireEvent.click(gameMode);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await flushOperations();

    const onSettingsChanged = bridge.settings_changed.connect.mock.calls[0][0];
    act(() =>
      onSettingsChanged(
        JSON.stringify({
          game_mode_enabled: false,
          __fenestraTransaction: cleanedTransaction,
        }),
      ),
    );

    expect(gameMode).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("Keeps a newer edit dirty when an earlier commit finishes.", async () => {
    const bridge = makeBridge();
    let finishCommit;
    bridge.commit_draft.mockImplementation((transactionId, cb) => {
      finishCommit = cb;
    });
    renderApp(bridge);
    const toggle = screen.getAllByRole("switch")[1];

    fireEvent.click(toggle);
    await flushOperations();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await flushOperations();
    fireEvent.click(toggle);
    act(() => finishCommit(JSON.stringify({ ok: true })));
    await flushOperations();

    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Earlier changes saved; newer changes remain.",
    );
  });

  it("Drops a queued theme patch when an immediate backend theme wins.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    renderApp(bridge);
    fireEvent.click(screen.getByRole("button", { name: "General" }));
    const onSettingsChanged = bridge.settings_changed.connect.mock.calls[0][0];

    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    await flushOperations();
    fireEvent.click(screen.getByRole("radio", { name: "System" }));
    act(() => onSettingsChanged(JSON.stringify({ theme: "light", game_mode_enabled: true })));
    act(() => onSettingsChanged(JSON.stringify({ theme: "dark", game_mode_enabled: true })));
    act(() => vi.advanceTimersByTime(500));
    await flushOperations();

    expect(bridge.save_settings).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  });

  it("Retains recoverability when discard fails with a trailing edit.", async () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    bridge.discard_draft.mockImplementation((transactionId, cb) =>
      cb(JSON.stringify({ ok: false, error: "discard rejected" })),
    );
    renderApp(bridge);
    const toggle = screen.getAllByRole("switch")[1];

    fireEvent.click(toggle);
    await flushOperations();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await flushOperations();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await flushOperations();

    expect(JSON.parse(bridge.save_settings.mock.calls.at(-1)[0])).toEqual({
      game_mode_enabled: true,
    });
    expect(bridge.commit_draft).toHaveBeenCalledTimes(1);
  });
});

describe("frontend lifecycle and temporary surfaces", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("Keeps title-bar controls inside the native interactive exclusions.", () => {
    const bridge = makeBridge();
    renderApp(bridge);

    const search = screen.getByRole("button", {
      name: "Search or jump to commands",
    });
    const minimize = screen.getByRole("button", { name: "Minimize Fenestra" });
    expect(search.parentElement.style.width).toBe("320px");
    expect(minimize.parentElement.style.width).toBe("72px");
    // The buttons fill the title bar above its border instead of overflowing it.
    expect(minimize.parentElement.style.alignSelf).toBe("stretch");
    expect(minimize.style.height).toBe("100%");
    expect(screen.getByRole("button", { name: "Close Fenestra" }).style.height).toBe("100%");
  });

  it("Reports measured title-bar regions to the backend.", () => {
    const original = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
      const region = this.getAttribute?.("data-hit-test-region");
      if (region === "titlebar") {
        return { left: 0, right: 1000, top: 0, bottom: 34, width: 1000, height: 34 };
      }
      if (region === "interactive") {
        return { left: 0, right: 319.2, top: 0, bottom: 34, width: 319.2, height: 34 };
      }
      if (region === "controls") {
        return { left: 928.2, right: 1000, top: 0, bottom: 34, width: 71.8, height: 34 };
      }
      return original.call(this);
    };
    try {
      const bridge = makeBridge();
      renderApp(bridge);

      expect(bridge.set_hit_test_regions).toHaveBeenCalledWith(320, 72, 34, expect.any(Function));
    } finally {
      Element.prototype.getBoundingClientRect = original;
    }
  });

  it("Disconnects every bridge signal when the app unmounts.", () => {
    const bridge = makeBridge();
    const { unmount } = renderApp(bridge);
    unmount();

    for (const name of [
      "settings_changed",
      "dirty_changed",
      "snap_status",
      "capture_status",
      "views_status",
    ]) {
      expect(bridge[name].disconnect).toHaveBeenCalledWith(bridge[name].connect.mock.calls[0][0]);
    }
  });

  it("Keeps a zero-timeout backend status visible.", () => {
    vi.useFakeTimers();
    const bridge = makeBridge();
    renderApp(bridge);
    const onStatus = bridge.snap_status.connect.mock.calls[0][0];

    act(() => onStatus("Press a key...", 0));
    act(() => vi.advanceTimersByTime(5000));

    expect(screen.getByText("Press a key...")).toBeInTheDocument();
  });

  it("Styles backend status messages by their explicit tone, not their wording.", () => {
    const bridge = makeBridge();
    renderApp(bridge);
    const onSnapStatus = bridge.snap_status.connect.mock.calls[0][0];
    const onViewsStatus = bridge.views_status.connect.mock.calls[0][0];

    act(() => onSnapStatus("Snapping failed on purpose in this sentence.", 3000, "neutral"));
    expect(screen.getByRole("status")).toHaveTextContent("failed on purpose");
    expect(screen.getByRole("alert")).toBeEmptyDOMElement();

    act(() => onSnapStatus("The window is not responding.", 3000, "error"));
    expect(screen.getByRole("alert")).toHaveTextContent("The window is not responding.");

    act(() => onViewsStatus("Folder-view update failed. Locked.", 8000, "error"));
    expect(screen.getByRole("alert")).toHaveTextContent("Folder-view update failed. Locked.");

    act(() => onViewsStatus("Details is now the default view.", 6000, "success"));
    expect(screen.getByRole("status")).toHaveTextContent("Details is now the default view.");
    expect(screen.getByRole("alert")).toBeEmptyDOMElement();
  });

  it("Uses crisp, full-height window controls with a legible close hover.", () => {
    const bridge = makeBridge();
    renderApp(bridge);
    const close = screen.getByRole("button", { name: "Close Fenestra" });

    expect(close.querySelector("svg")).toBeInTheDocument();
    fireEvent.mouseEnter(close);
    expect(close).toHaveStyle({ background: "#E81123", color: "#FFFFFF" });
  });

  it("Saves pending changes with Ctrl+S.", async () => {
    const bridge = makeBridge();
    renderApp(bridge);
    fireEvent.click(screen.getAllByRole("switch")[1]);

    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await flushOperations();

    expect(bridge.commit_draft).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Save changes" })).toHaveAttribute(
      "aria-keyshortcuts",
      "Control+S",
    );
  });

  it("Suppresses Ctrl+S while capture or a temporary surface is active.", async () => {
    const bridge = makeBridge();
    renderApp(bridge);
    fireEvent.click(screen.getAllByRole("switch")[1]);
    fireEvent.click(screen.getByRole("button", { name: "Snap key: SHIFT" }));
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await flushOperations();
    expect(bridge.commit_draft).not.toHaveBeenCalled();

    const onCaptureStatus = bridge.capture_status.connect.mock.calls[0][0];
    act(() => onCaptureStatus("cancelled"));
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await flushOperations();
    expect(bridge.commit_draft).not.toHaveBeenCalled();
  });

  it("Cancels active key capture when the Snap page unmounts.", () => {
    const bridge = makeBridge();
    renderApp(bridge);

    fireEvent.click(screen.getByRole("button", { name: "Snap key: SHIFT" }));
    fireEvent.click(screen.getByRole("button", { name: "Explorer" }));

    expect(bridge.cancel_capture).toHaveBeenCalledTimes(1);
  });

  it("Does not open the command palette during key capture.", () => {
    const bridge = makeBridge();
    renderApp(bridge);

    fireEvent.click(screen.getByRole("button", { name: "Snap key: SHIFT" }));
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(screen.queryByPlaceholderText(/search settings/i)).not.toBeInTheDocument();
    expect(bridge.set_modal_open).toHaveBeenLastCalledWith(true, expect.any(Function));
  });

  it("Does not open the command palette over a confirmation modal.", () => {
    const bridge = makeBridge();
    renderApp(bridge);

    fireEvent.click(screen.getByRole("button", { name: "General" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(bridge.set_modal_open).toHaveBeenLastCalledWith(true, expect.any(Function));
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(screen.queryByPlaceholderText(/search settings/i)).not.toBeInTheDocument();
  });

  it("Guards native shortcuts while the command palette is open.", () => {
    const bridge = makeBridge();
    renderApp(bridge);

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(screen.getByRole("dialog", { name: "Command palette" })).toBeVisible();
    expect(bridge.set_modal_open).toHaveBeenLastCalledWith(true, expect.any(Function));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(bridge.set_modal_open).toHaveBeenLastCalledWith(false, expect.any(Function));
  });

  it("Lets an immediate backend theme win while a tagged draft call is pending.", async () => {
    const bridge = makeBridge();
    let finishStage;
    bridge.save_settings.mockImplementation((json, transactionId, cb) => {
      finishStage = cb;
    });
    renderApp(bridge);
    fireEvent.click(screen.getByRole("button", { name: "General" }));
    const onSettingsChanged = bridge.settings_changed.connect.mock.calls[0][0];

    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    await flushOperations();
    act(() => onSettingsChanged(JSON.stringify({ theme: "dark" })));

    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    act(() => finishStage(JSON.stringify({ ok: true })));
    await flushOperations();
    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  });

  it("Includes each key-capture purpose in its accessible name.", () => {
    const bridge = makeBridge();
    renderApp(bridge);

    expect(screen.getByRole("button", { name: "Snap key: SHIFT" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore key: CTRL" })).toBeInTheDocument();
  });

  it("Disables Save until a change exists and reports successful saves.", async () => {
    const bridge = makeBridge();
    renderApp(bridge);
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getAllByRole("switch")[1]);
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await flushOperations();

    expect(save).toBeDisabled();
    expect(screen.getByText("Changes saved.")).toBeInTheDocument();
  });

  it("Commits a captured key when it is the only unsaved change.", async () => {
    const bridge = makeBridge();
    renderApp(bridge);
    const onDirtyChanged = bridge.dirty_changed.connect.mock.calls[0][0];
    act(() => onDirtyChanged(true));

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await flushOperations();

    expect(bridge.save_settings).not.toHaveBeenCalled();
    expect(bridge.commit_draft).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Changes saved.")).toBeInTheDocument();
  });

  it("Does not mark settings unsaved when the current value is chosen again.", async () => {
    const bridge = makeBridge();
    renderApp(bridge);
    fireEvent.click(screen.getByRole("button", { name: "General" }));
    fireEvent.click(screen.getByRole("radio", { name: "Cozy" }));
    await flushOperations();

    expect(bridge.save_settings).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("Shows the saved snap state in the sidebar and palette until Save.", async () => {
    const bridge = makeBridge();
    const saved = { enable_snap: true, game_mode_enabled: true };
    bridge.get_settings.mockImplementation((cb) =>
      cb(JSON.stringify({ ok: true, data: { ...saved, __fenestraSaved: saved } })),
    );
    renderApp(bridge);
    const onSettingsChanged = bridge.settings_changed.connect.mock.calls[0][0];
    const snapPage = screen.getByRole("button", { name: /Window snap/ });
    expect(within(snapPage).getByText("On")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("switch")[0]);
    await flushOperations();
    const draft = { enable_snap: false, game_mode_enabled: true };
    act(() => onSettingsChanged(JSON.stringify({ ...draft, __fenestraSaved: saved })));

    expect(screen.getAllByRole("switch")[0]).toHaveAttribute("aria-checked", "false");
    expect(within(snapPage).getByText("On")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("option", { name: "Disable snap" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await flushOperations();
    act(() => onSettingsChanged(JSON.stringify({ ...draft, __fenestraSaved: draft })));

    expect(within(snapPage).queryByText("On")).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("option", { name: "Enable snap" })).toBeInTheDocument();
  });

  it("Makes the scrolling page a keyboard stop with room for focus rings.", () => {
    renderApp(makeBridge());
    const main = screen.getByRole("main");

    expect(main).toHaveAttribute("tabindex", "0");
    expect(main).toHaveStyle({ outlineOffset: "-2px", scrollPaddingBlock: "12px" });
  });

  it("Keeps both status live regions mounted before any message.", () => {
    renderApp(makeBridge());

    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    expect(screen.getByRole("alert")).toBeEmptyDOMElement();
  });
});

describe("Explorer default folder view actions", () => {
  function startApply() {
    fireEvent.click(screen.getByRole("button", { name: "Make Details the default" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply and restart Explorer" }));
  }

  it("Shows an in-progress message instead of success when apply starts.", () => {
    const bridge = makeBridge();
    renderApp(bridge);
    fireEvent.click(screen.getByRole("button", { name: "Explorer" }));

    startApply();

    expect(bridge.apply_details_view).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText("Applying Details view. File Explorer will restart..."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Working..." })).toBeDisabled();
    const onViewsStatus = bridge.views_status.connect.mock.calls[0][0];
    act(() => onViewsStatus("Details is now the default view for all folders.", 6000));
    expect(screen.getByRole("button", { name: "Make Details the default" })).toBeEnabled();
  });

  it("Keeps a running folder-view action busy across navigation.", () => {
    const bridge = makeBridge();
    renderApp(bridge);
    fireEvent.click(screen.getByRole("button", { name: "Explorer" }));
    startApply();

    fireEvent.click(screen.getByRole("button", { name: "General" }));
    fireEvent.click(screen.getByRole("button", { name: "Explorer" }));

    expect(screen.getByRole("button", { name: "Working..." })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Reset folder views to Windows defaults" }),
    ).toBeDisabled();
    expect(bridge.apply_details_view).toHaveBeenCalledTimes(1);
    const onViewsStatus = bridge.views_status.connect.mock.calls[0][0];
    act(() => onViewsStatus("Details is now the default view for all folders.", 6000));
    expect(screen.getByRole("button", { name: "Make Details the default" })).toBeEnabled();
  });

  it("Surfaces a backend error and allows another attempt.", () => {
    const bridge = makeBridge();
    bridge.apply_details_view.mockImplementation((cb) =>
      cb(JSON.stringify({ ok: false, error: "Explorer restart failed." })),
    );
    renderApp(bridge);
    fireEvent.click(screen.getByRole("button", { name: "Explorer" }));

    startApply();

    expect(screen.getByRole("alert")).toHaveTextContent("Explorer restart failed.");
    expect(screen.getByRole("button", { name: "Make Details the default" })).toBeEnabled();
  });

  it("Reports an unsupported backend when the method is missing.", () => {
    const bridge = makeBridge();
    delete bridge.apply_details_view;
    renderApp(bridge);
    fireEvent.click(screen.getByRole("button", { name: "Explorer" }));

    startApply();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Folder view changes are not supported by this backend build.",
    );
    expect(screen.getByRole("button", { name: "Make Details the default" })).toBeEnabled();
  });
});
