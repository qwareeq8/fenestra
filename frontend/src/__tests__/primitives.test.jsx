import { afterEach, describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { DARK, LIGHT, ThemeProvider } from "../theme.jsx";
import { Toggle, Button, Card, Row, Segmented, Stepper, Slider, Badge } from "../primitives.jsx";

// Wrap each component in ThemeProvider with the minimum required tweaks.
function renderWithTheme(ui, theme = "dark") {
  const tweaks = { theme, accent: "amber", density: "cozy", radius: 4 };
  return render(
    <ThemeProvider tweaks={tweaks} setTweaks={vi.fn()}>
      {ui}
    </ThemeProvider>,
  );
}

describe("Toggle", () => {
  it("Renders the off state.", () => {
    renderWithTheme(<Toggle on={false} onChange={vi.fn()} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("Renders the on state.", () => {
    renderWithTheme(<Toggle on={true} onChange={vi.fn()} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("Uses perceivable off-state tracks in both themes.", () => {
    const { unmount } = renderWithTheme(<Toggle on={false} onChange={vi.fn()} />, "light");
    // Both tracks keep at least 3:1 contrast with the surfaces behind them.
    expect(screen.getByRole("switch").firstElementChild).toHaveStyle({
      background: "#858B95",
    });
    unmount();
    renderWithTheme(<Toggle on={false} onChange={vi.fn()} />, "dark");
    expect(screen.getByRole("switch").firstElementChild).toHaveStyle({
      background: "#6B717C",
    });
  });
});

describe("Segmented", () => {
  it("Uses radio semantics and arrow-key selection for exclusive choices.", () => {
    const onChange = vi.fn();
    renderWithTheme(
      <Row label="Theme">
        <Segmented options={["System", "Light", "Dark"]} value="System" onChange={onChange} />
      </Row>,
    );
    const group = screen.getByRole("radiogroup", { name: "Theme" });
    const system = screen.getByRole("radio", { name: "System" });
    const light = screen.getByRole("radio", { name: "Light" });

    expect(group).toContainElement(system);
    expect(system).toHaveAttribute("aria-checked", "true");
    expect(system).toHaveAttribute("tabindex", "0");
    expect(light).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(system, { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("Light");
    expect(light).toHaveFocus();
  });
});

describe("Target sizes", () => {
  it("Gives switches, sliders, and stepper fields 24-pixel targets.", () => {
    renderWithTheme(
      <>
        <Row label="Enable snap">
          <Toggle on onChange={vi.fn()} />
        </Row>
        <Row label="Width">
          <Slider value={76} onChange={vi.fn()} min={10} max={100} />
        </Row>
      </>,
    );

    expect(screen.getByRole("switch", { name: "Enable snap" })).toHaveStyle({
      minWidth: "24px",
      minHeight: "24px",
    });
    expect(screen.getByRole("slider", { name: "Width" })).toHaveStyle({ height: "24px" });
  });

  it("Names the stepper input directly so a scrolled-out row label cannot unname it.", () => {
    renderWithTheme(
      <Row label="Press count">
        <Stepper value={3} onChange={vi.fn()} min={1} max={10} />
      </Row>,
    );

    expect(screen.getByRole("spinbutton", { name: "Press count" })).toHaveAttribute(
      "aria-label",
      "Press count",
    );
  });
});

describe("Stepper", () => {
  afterEach(() => vi.useRealTimers());

  it("Commits an edited value on blur and clamps it to the configured range.", () => {
    const onChange = vi.fn();
    renderWithTheme(
      <Row label="Interval">
        <Stepper value={1050} onChange={onChange} min={100} max={5000} step={50} suffix="ms" />
      </Row>,
    );
    const input = screen.getByRole("spinbutton", { name: "Interval" });

    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "5075" } });
    fireEvent.blur(input);

    expect(onChange).toHaveBeenCalledWith(5000);
  });

  it("Reverts an empty edit instead of changing the value to the minimum.", () => {
    const onChange = vi.fn();
    renderWithTheme(
      <Row label="Interval">
        <Stepper value={1050} onChange={onChange} min={100} max={5000} step={50} suffix="ms" />
      </Row>,
    );
    const input = screen.getByRole("spinbutton", { name: "Interval" });

    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    expect(onChange).not.toHaveBeenCalled();
    expect(input).toHaveValue(1050);
  });

  it("Exposes a visible unit through the spinbutton value text.", () => {
    renderWithTheme(
      <Row label="Interval">
        <Stepper value={1050} onChange={vi.fn()} min={100} max={5000} step={50} suffix="ms" />
      </Row>,
    );

    expect(screen.getByRole("spinbutton", { name: "Interval" })).toHaveAttribute(
      "aria-valuetext",
      "1050 ms",
    );
    expect(
      screen.getByRole("button", { name: "Decrease Interval" }).querySelector("svg"),
    ).toBeInTheDocument();
  });

  it("Sizes the input to its digits so the field can center the value.", () => {
    renderWithTheme(
      <Row label="Press count">
        <Stepper value={3} onChange={vi.fn()} min={1} max={10} />
      </Row>,
    );
    const input = screen.getByRole("spinbutton", { name: "Press count" });
    const field = input.closest("label");

    expect(input).toHaveStyle({ width: "calc(1ch + 2px)", textAlign: "center" });
    expect(field).toHaveStyle({ display: "flex", justifyContent: "center" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "10" } });
    expect(input).toHaveStyle({ width: "calc(2ch + 2px)" });
  });

  it("Centers the value and its unit together as one group.", () => {
    renderWithTheme(
      <Row label="Interval">
        <Stepper value={1050} onChange={vi.fn()} min={100} max={5000} step={50} suffix="ms" />
      </Row>,
    );
    const input = screen.getByRole("spinbutton", { name: "Interval" });
    const field = input.closest("label");

    expect(input).toHaveStyle({ width: "calc(4ch + 2px)" });
    expect([...field.children].map((child) => child.textContent || child.tagName)).toEqual([
      "INPUT",
      "ms",
    ]);
    expect(field).toHaveStyle({ justifyContent: "center" });
  });

  it("Repeats safely while an increment button is held.", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    renderWithTheme(
      <Row label="Interval">
        <Stepper value={100} onChange={onChange} min={100} max={5000} step={50} />
      </Row>,
    );
    const increase = screen.getByRole("button", { name: "Increase Interval" });

    fireEvent.pointerDown(increase, { button: 0, pointerId: 1 });
    act(() => vi.advanceTimersByTime(525));
    fireEvent.pointerUp(increase, { pointerId: 1 });
    const callsAfterHold = onChange.mock.calls.length;
    fireEvent.click(increase);

    expect(callsAfterHold).toBeGreaterThanOrEqual(2);
    expect(onChange).toHaveBeenLastCalledWith(250);
    expect(onChange).toHaveBeenCalledTimes(callsAfterHold);
  });
});

describe("Button", () => {
  it("Keeps the shortcut hint out of the name and wraps its keys for centering.", () => {
    renderWithTheme(<Button kbd="Ctrl S">Save changes</Button>);
    const button = screen.getByRole("button", { name: "Save changes" });
    const hint = button.querySelector("[data-shortcut-hint]");

    expect(button.querySelector("[data-button-label]")).toHaveTextContent("Save changes");
    expect(hint).toHaveAttribute("aria-hidden", "true");
    expect(hint.querySelector("[data-glyphs]")).toHaveTextContent("Ctrl S");
  });

  it("Uses its child text as its accessible name.", () => {
    renderWithTheme(<Button>Click me</Button>);
    expect(screen.getByRole("button", { name: "Click me" })).toBeVisible();
  });

  it("Renders the primary variant.", () => {
    renderWithTheme(<Button variant="primary">Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeVisible();
  });

  it("Forwards accessible disclosure attributes.", () => {
    renderWithTheme(
      <Button aria-expanded="false" aria-controls="details">
        Details
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Details" })).toHaveAttribute(
      "aria-controls",
      "details",
    );
  });
});

describe("Card", () => {
  it("Renders its title and children.", () => {
    renderWithTheme(
      <Card title="Test Card">
        <p>Card content</p>
      </Card>,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Test Card" })).toBeInTheDocument();
    expect(screen.getByText("Card content")).toBeInTheDocument();
  });

  it("Renders without a title.", () => {
    renderWithTheme(
      <Card>
        <p>Just content</p>
      </Card>,
    );
    expect(screen.getByText("Just content")).toBeInTheDocument();
  });
});

describe("Badge", () => {
  it("Renders with the default tone.", () => {
    renderWithTheme(<Badge>NEW</Badge>);
    expect(screen.getByText("NEW")).toBeInTheDocument();
  });

  it("Renders with the accent tone.", () => {
    renderWithTheme(<Badge tone="accent">ON</Badge>);
    expect(screen.getByText("ON")).toBeInTheDocument();
  });
});

describe("Slider", () => {
  function renderSlider(onChange = vi.fn()) {
    renderWithTheme(<Slider value={50} onChange={onChange} />);
    const slider = screen.getByRole("slider");
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({
      left: 0,
      width: 100,
      top: 0,
      right: 100,
      bottom: 20,
      height: 20,
      x: 0,
      y: 0,
      toJSON: () => {},
    });
    slider.setPointerCapture = vi.fn();
    slider.hasPointerCapture = vi.fn(() => true);
    slider.releasePointerCapture = vi.fn();
    return { slider, onChange };
  }

  it("Ignores non-primary pointer buttons.", () => {
    const { slider, onChange } = renderSlider();

    fireEvent.pointerDown(slider, { button: 2, clientX: 80, pointerId: 1 });

    expect(onChange).not.toHaveBeenCalled();
    expect(slider.setPointerCapture).not.toHaveBeenCalled();
  });

  it("Stops tracking a pointer after pointer cancellation.", () => {
    const { slider, onChange } = renderSlider();

    fireEvent.pointerDown(slider, { button: 0, clientX: 20, pointerId: 7 });
    expect(onChange).toHaveBeenCalledTimes(1);
    fireEvent.pointerCancel(slider, { pointerId: 7 });
    fireEvent.pointerMove(slider, { clientX: 90, pointerId: 7 });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(slider.releasePointerCapture).toHaveBeenCalledWith(7);
  });

  it("Uses its visual row label as its accessible name.", () => {
    renderWithTheme(
      <Row label="Window width">
        <Slider value={50} onChange={vi.fn()} />
      </Row>,
    );

    expect(screen.getByRole("slider", { name: "Window width" })).toBeVisible();
  });

  it("Supports standard horizontal slider keyboard controls.", () => {
    const onChange = vi.fn();
    renderWithTheme(<Slider value={50} onChange={onChange} min={10} max={90} />);
    const slider = screen.getByRole("slider");

    fireEvent.keyDown(slider, { key: "ArrowUp" });
    fireEvent.keyDown(slider, { key: "ArrowDown" });
    fireEvent.keyDown(slider, { key: "Home" });
    fireEvent.keyDown(slider, { key: "End" });
    fireEvent.keyDown(slider, { key: "PageDown" });
    fireEvent.keyDown(slider, { key: "PageUp" });

    expect(onChange.mock.calls.map(([value]) => value)).toEqual([51, 49, 10, 90, 42, 58]);
  });

  it("Uses the row description as its accessible description.", () => {
    renderWithTheme(
      <Row label="Window width" description="Percentage of screen width.">
        <Slider value={50} onChange={vi.fn()} />
      </Row>,
    );

    expect(screen.getByRole("slider", { name: "Window width" })).toHaveAccessibleDescription(
      "Percentage of screen width.",
    );
  });
});

describe("Row control names", () => {
  it("Names an otherwise empty switch from its row.", () => {
    renderWithTheme(
      <Row label="Enable snap">
        <Toggle on={true} onChange={vi.fn()} />
      </Row>,
    );

    expect(screen.getByRole("switch", { name: "Enable snap" })).toBeVisible();
  });
});

function relativeLuminance(hex) {
  const channels = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const linear = channels.map((value) =>
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground, background) {
  const values = [relativeLuminance(foreground), relativeLuminance(background)];
  return (Math.max(...values) + 0.05) / (Math.min(...values) + 0.05);
}

describe("semantic text contrast", () => {
  it("Keeps muted and danger text at WCAG AA contrast in both themes.", () => {
    expect(contrastRatio(LIGHT.textMuted, LIGHT.sidebar)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(DARK.textMuted, DARK.surface)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(LIGHT.dangerText, LIGHT.surface)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(DARK.dangerText, DARK.surface)).toBeGreaterThanOrEqual(4.5);
  });
});
