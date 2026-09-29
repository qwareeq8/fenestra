import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "../theme.jsx";
import { CommandPalette } from "../panels.jsx";

// Wrap each component in ThemeProvider with the minimum required tweaks.
function renderPalette(props = {}) {
  const tweaks = { theme: "dark", accent: "amber", density: "cozy", radius: 4 };
  const setTweaks = vi.fn();
  const app = {
    snapEnabled: true,
    gameMode: true,
    saved: { snapEnabled: true, gameMode: true },
    unsaved: true,
    set: vi.fn(),
    ...props.app,
  };
  const defaults = {
    open: true,
    onClose: vi.fn(),
    setNav: vi.fn(),
    onTestSnap: vi.fn(),
    onSave: vi.fn(),
    onReset: vi.fn(),
    ...props,
    app,
  };
  return render(
    <ThemeProvider tweaks={tweaks} setTweaks={setTweaks}>
      <CommandPalette {...defaults} />
    </ThemeProvider>,
  );
}

describe("CommandPalette", () => {
  it("Renders the search input when open.", () => {
    renderPalette();
    const input = screen.getByRole("combobox", { name: "Search commands" });
    expect(input).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Command palette" })).toContainElement(input);
    expect(screen.getByRole("listbox", { name: "Commands" })).toBeVisible();
    expect(screen.getAllByRole("option").length).toBeGreaterThan(10);
  });

  it("Shows all commands when no filter is applied.", () => {
    renderPalette();
    expect(screen.getByRole("option", { name: "Go to Window snap" })).toBeVisible();
    expect(screen.getByRole("option", { name: "Go to Explorer" })).toBeVisible();
    expect(screen.getByRole("option", { name: /Test snap/ })).toBeVisible();
    expect(screen.getByRole("option", { name: "Save changes" })).toBeVisible();
  });

  it("Does not offer Save when there are no unsaved changes.", () => {
    renderPalette({ app: { unsaved: false } });
    expect(screen.queryByRole("option", { name: "Save changes" })).not.toBeInTheDocument();
  });

  it("Does not offer Save while a save is already in progress.", () => {
    renderPalette({ app: { unsaved: true, saving: true } });
    expect(screen.queryByRole("option", { name: "Save changes" })).not.toBeInTheDocument();
  });

  it("Describes the saved snap and game mode settings, not the draft.", async () => {
    const set = vi.fn();
    renderPalette({
      app: {
        snapEnabled: false,
        gameMode: true,
        saved: { snapEnabled: true, gameMode: false },
        set,
      },
    });

    expect(screen.getByRole("option", { name: "Disable snap" })).toBeVisible();
    expect(screen.getByRole("option", { name: "Enable game mode" })).toBeVisible();
    await userEvent.click(screen.getByRole("option", { name: "Enable game mode" }));
    expect(set).toHaveBeenCalledWith({ gameMode: true });
  });

  it("Filters commands when typing a search query.", async () => {
    renderPalette();
    const user = userEvent.setup();
    const input = screen.getByRole("combobox", { name: "Search commands" });
    await user.type(input, "snap");
    expect(screen.getByRole("option", { name: /Test snap/ })).toBeVisible();
    expect(screen.getByRole("option", { name: "Go to Window snap" })).toBeVisible();
    expect(screen.queryByRole("option", { name: "Go to Explorer" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Go to About" })).not.toBeInTheDocument();
  });

  it("Shows a no-results message for an unmatched filter.", async () => {
    renderPalette();
    const user = userEvent.setup();
    const input = screen.getByRole("combobox", { name: "Search commands" });
    await user.type(input, "zzzznonexistent");
    expect(screen.getByRole("status")).toHaveTextContent('No results for "zzzznonexistent".');
  });

  it("Does not render when open is false.", () => {
    renderPalette({ open: false });
    expect(screen.queryByRole("dialog", { name: "Command palette" })).not.toBeInTheDocument();
  });

  it("Highlights the hovered item instead of the last item.", async () => {
    const setNav = vi.fn();
    renderPalette({ setNav });
    const user = userEvent.setup();
    const first = screen.getByRole("option", { name: "Go to Window snap" });
    const second = screen.getByRole("option", { name: "Go to Explorer" });
    await user.hover(second);
    // The hovered row becomes active and the previously active row clears.
    expect(second.style.background).not.toBe("transparent");
    expect(first.style.background).toBe("transparent");
    // Enter runs the hovered command, proving the hover index is correct.
    await user.keyboard("{Enter}");
    expect(setNav).toHaveBeenCalledWith("exp");
  });

  it("Scrolls the keyboard-highlighted command into view.", async () => {
    const original = Element.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    try {
      renderPalette();
      scrollIntoView.mockClear();
      const user = userEvent.setup();

      await user.keyboard("{ArrowDown}");

      expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
    } finally {
      if (original) Element.prototype.scrollIntoView = original;
      else delete Element.prototype.scrollIntoView;
    }
  });

  it("Keeps the palette open when Enter has no matching command.", async () => {
    const onClose = vi.fn();
    renderPalette({ onClose });
    const user = userEvent.setup();
    await user.type(screen.getByRole("combobox", { name: "Search commands" }), "zzzznonexistent");

    await user.keyboard("{Enter}");

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Command palette" })).toBeVisible();
  });

  it("Does not execute a command for a modified Enter key.", async () => {
    const setNav = vi.fn();
    const onClose = vi.fn();
    renderPalette({ setNav, onClose });
    const user = userEvent.setup();

    await user.keyboard("{Control>}{Enter}{/Control}");

    expect(setNav).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
