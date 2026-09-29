import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "../theme.jsx";
import { AboutPage, ShortcutsPage, ExplorerPage, SnapPage } from "../pages.jsx";

// Wrap the component under test in a ThemeProvider with minimal tweaks.
function renderWithTheme(ui) {
  const tweaks = { theme: "dark", accent: "amber", density: "cozy", radius: 4 };
  return render(
    <ThemeProvider tweaks={tweaks} setTweaks={vi.fn()}>
      {ui}
    </ThemeProvider>,
  );
}

function makeApp(overrides = {}) {
  return {
    snapEnabled: true,
    snapKey: "SHIFT",
    restoreKey: "CTRL",
    pressCount: 3,
    interval: 1050,
    width: 76,
    height: 76,
    gameMode: true,
    autoSize: true,
    launchLogin: false,
    accent: "amber",
    density: "cozy",
    themeMode: "system",
    captureActive: false,
    setCaptureActive: vi.fn(),
    setModalOpen: vi.fn(),
    set: vi.fn(),
    onTestSnap: vi.fn(),
    onReset: vi.fn(),
    showStatus: vi.fn(),
    viewsBusy: null,
    runViewsAction: vi.fn(),
    bridge: {
      capture_key: vi.fn((target, cb) => cb(JSON.stringify({ ok: true }))),
      cancel_capture: vi.fn((cb) => cb(JSON.stringify({ ok: true }))),
      capture_status: { connect: vi.fn(), disconnect: vi.fn() },
    },
    ...overrides,
  };
}

describe("ShortcutsPage", () => {
  it("Renders one snap key chip per press in both snap and restore rows.", () => {
    renderWithTheme(<ShortcutsPage app={makeApp({ pressCount: 5 })} />);
    // The trigger row and the restore row each show the snap key once per
    // press, so the snap key appears twice per configured press.
    expect(screen.getAllByText("SHIFT")).toHaveLength(10);
  });

  it("Shows the restore key as one held modifier.", () => {
    renderWithTheme(<ShortcutsPage app={makeApp({ pressCount: 5 })} />);
    expect(screen.getAllByText("CTRL")).toHaveLength(1);
    expect(screen.getByText("(hold)")).toBeInTheDocument();
    expect(screen.getByText("Hold CTRL while tapping SHIFT 5 times.")).toBeInTheDocument();
  });

  it("Uses singular copy and one chip pair when pressCount is 1.", () => {
    renderWithTheme(<ShortcutsPage app={makeApp({ pressCount: 1 })} />);
    expect(screen.getAllByText("SHIFT")).toHaveLength(2);
    expect(screen.getAllByText("CTRL")).toHaveLength(1);
    expect(screen.getByText("Hold CTRL while tapping SHIFT once.")).toBeInTheDocument();
  });

  it("Wraps large shortcut sequences inside the supported window width.", () => {
    renderWithTheme(<ShortcutsPage app={makeApp({ pressCount: 10 })} />);
    const triggerRow = screen.getByText("Trigger snap").parentElement.parentElement;
    const chips = triggerRow.lastElementChild;

    expect(triggerRow.style.flexWrap).toBe("wrap");
    expect(chips.style.flexWrap).toBe("wrap");
    expect(chips.style.minWidth).toBe("0px");
  });

  it("Labels shortcut rows with the same type as settings rows.", () => {
    renderWithTheme(<ShortcutsPage app={makeApp()} />);

    expect(screen.getByText("Trigger snap")).toHaveStyle({ fontSize: "13.5px", fontWeight: "700" });
    expect(screen.getByText("Tap SHIFT 3 times.")).toHaveStyle({ fontSize: "12.5px" });
  });

  it("Separates global gestures from the complete in-app shortcut list.", () => {
    renderWithTheme(<ShortcutsPage app={makeApp()} />);

    expect(screen.getByRole("heading", { level: 2, name: "Global snap gestures" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "In-app shortcuts" })).toBeVisible();
    for (const label of ["Command palette", "Save changes", "Toggle theme", "Test snap", "Help"]) {
      expect(screen.getByText(label)).toBeVisible();
    }
  });
});

describe("SnapPage size sliders", () => {
  it("Captions the monitor preview with its size below the stand.", () => {
    renderWithTheme(<SnapPage app={makeApp({ width: 64, height: 58 })} />);

    expect(screen.getByText("64% × 58%")).toBeInTheDocument();
  });

  it("Limits the width and height sliders to the backend range 10 to 100.", () => {
    renderWithTheme(<SnapPage app={makeApp()} />);
    const sliders = screen.getAllByRole("slider");
    expect(sliders).toHaveLength(2);
    for (const slider of sliders) {
      expect(slider).toHaveAttribute("aria-valuemin", "10");
      expect(slider).toHaveAttribute("aria-valuemax", "100");
    }
    expect(screen.getByRole("slider", { name: "Width" })).toBeVisible();
    expect(screen.getByRole("slider", { name: "Height" })).toBeVisible();
  });

  it("Clears capture state and reports a rejected second capture.", async () => {
    const app = makeApp();
    app.bridge.capture_key = vi.fn((target, cb) =>
      cb(
        JSON.stringify({
          ok: false,
          error: "Key capture is already in progress.",
        }),
      ),
    );
    renderWithTheme(<SnapPage app={app} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Snap key: SHIFT" }));

    expect(screen.getByRole("button", { name: "Snap key: SHIFT" })).toBeVisible();
    expect(app.setCaptureActive).toHaveBeenLastCalledWith(false);
    expect(app.showStatus).toHaveBeenCalledWith(
      "Key capture is already in progress.",
      3000,
      "error",
    );
  });
});

describe("ExplorerPage default folder view", () => {
  it("Renders the section title, body copy, and both buttons.", () => {
    renderWithTheme(<ExplorerPage app={makeApp()} />);
    expect(screen.getByText("Default folder view")).toBeInTheDocument();
    expect(screen.getByText(/keeps the desktop icon layout/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make Details the default" })).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: "Reset folder views to Windows defaults",
      }),
    ).toBeVisible();
  });

  it("Runs the apply action through the application after confirmation.", async () => {
    const app = makeApp();
    renderWithTheme(<ExplorerPage app={app} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Make Details the default" }));
    const dialog = screen.getByRole("dialog", {
      name: "Make Details the default?",
    });
    expect(
      screen.getByText(/Finish any file copies, moves, or deletions first/),
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", {
        name: "Apply and restart Explorer",
      }),
    );

    expect(app.runViewsAction).toHaveBeenCalledWith("apply");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Runs the reset action through the application after confirmation.", async () => {
    const app = makeApp();
    renderWithTheme(<ExplorerPage app={app} />);
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", {
        name: "Reset folder views to Windows defaults",
      }),
    );
    const dialog = screen.getByRole("dialog", { name: "Reset folder views?" });
    await user.click(
      within(dialog).getByRole("button", {
        name: "Reset and restart Explorer",
      }),
    );

    expect(app.runViewsAction).toHaveBeenCalledWith("reset");
  });

  it("Shows the running action as a disabled working button.", () => {
    renderWithTheme(<ExplorerPage app={makeApp({ viewsBusy: "reset" })} />);

    expect(screen.getByRole("button", { name: "Working..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Make Details the default" })).toBeDisabled();
  });

  it("Moves focus into the confirmation and restores the opener.", async () => {
    renderWithTheme(<ExplorerPage app={makeApp()} />);
    const user = userEvent.setup();
    const opener = screen.getByRole("button", {
      name: "Make Details the default",
    });

    await user.click(opener);
    const dialog = screen.getByRole("dialog", {
      name: "Make Details the default?",
    });
    expect(dialog).toContainElement(document.activeElement);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(document.activeElement).toBe(opener);
  });

  it("Moves focus to a stable action region when folder-view work starts.", async () => {
    renderWithTheme(<ExplorerPage app={makeApp()} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Make Details the default" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Make Details the default?" })).getByRole(
        "button",
        { name: "Apply and restart Explorer" },
      ),
    );

    expect(screen.getByRole("group", { name: "Default folder view actions" })).toHaveFocus();
  });

  it("Does not run an action when the dialog is cancelled.", async () => {
    const app = makeApp();
    renderWithTheme(<ExplorerPage app={app} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Make Details the default" }));
    const dialog = screen.getByRole("dialog", {
      name: "Make Details the default?",
    });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(app.runViewsAction).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("dialog", { name: "Make Details the default?" }),
    ).not.toBeInTheDocument();
  });
});

describe("AboutPage semantics", () => {
  it("Exposes the page heading and license disclosure state.", async () => {
    renderWithTheme(<AboutPage app={makeApp()} />);
    const user = userEvent.setup();

    expect(screen.getByRole("heading", { level: 1, name: "About" })).toBeVisible();
    const disclosure = screen.getByRole("button", { name: "View MIT license" });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    await user.click(disclosure);
    expect(screen.getByRole("button", { name: "Hide MIT license" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
