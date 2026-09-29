// Shared UI primitives for Fenestra. Every primitive reads useTokens(), so
// theme, accent, density, and radius changes reach them without props.

import React from "react";
import { useTokens } from "./theme.jsx";
import { Icon } from "./icons.jsx";

const RowControlContext = React.createContext(null);

/**
 * Trim a text box to the capital height and the baseline.
 *
 * Flex centering then centers the letters themselves rather than the font's
 * line box, whose ascent and descent differ between the WebCM faces and the
 * system fallbacks.
 */
const CAP_BOX = { textBox: "trim-both cap alphabetic" };

/**
 * Render a keyboard shortcut hint inside a button.
 *
 * @param {object} props
 * @param {string} props.keys - The shortcut, such as "Ctrl S".
 * @returns {JSX.Element} The hint, hidden from assistive technology.
 * @example
 * <ShortcutHint keys="Ctrl K" />
 */
function ShortcutHint({ keys }) {
  const t = useTokens();
  return (
    <span
      aria-hidden="true"
      data-shortcut-hint=""
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        height: 16,
        padding: "0 4px",
        // Only the outline is faded, so the keys keep the label's contrast.
        border: "1px solid color-mix(in srgb, currentColor 55%, transparent)",
        borderRadius: 3,
        fontSize: 10.5,
        fontWeight: 400,
        fontFamily: t.mono,
      }}
    >
      <span data-glyphs="" style={CAP_BOX}>
        {keys}
      </span>
    </span>
  );
}

/**
 * Render an on/off switch labeled by its enclosing Row.
 *
 * @param {object} props
 * @param {boolean} props.on - Whether the switch is on.
 * @param {(on: boolean) => void} props.onChange - Receives the requested state.
 * @param {"sm" | "md"} [props.size] - The switch size.
 * @returns {JSX.Element} The switch.
 * @example
 * <Toggle on={enabled} onChange={setEnabled} />
 */
function Toggle({ on, onChange, size = "md" }) {
  const t = useTokens();
  const row = React.useContext(RowControlContext);
  const trackWidth = size === "sm" ? 26 : 30;
  const trackHeight = size === "sm" ? 14 : 16;
  const knobSize = trackHeight - 4;
  return (
    <button
      role="switch"
      aria-checked={!!on}
      aria-labelledby={row?.labelId}
      aria-describedby={row?.descriptionId}
      onClick={(event) => {
        event.stopPropagation();
        onChange(!on);
      }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: 24,
        minHeight: 24,
        border: "none",
        padding: 0,
        cursor: "pointer",
        background: "transparent",
        flexShrink: 0,
      }}
    >
      <span
        style={{
          display: "block",
          width: trackWidth,
          height: trackHeight,
          borderRadius: trackHeight / 2,
          background: on ? t.accent : t.switchOff,
          position: "relative",
          transition: "background .15s",
        }}
      >
        <span
          style={{
            position: "absolute",
            top: 2,
            left: on ? trackWidth - knobSize - 2 : 2,
            width: knobSize,
            height: knobSize,
            borderRadius: knobSize / 2,
            background: on ? t.accentOn : t.surface,
            transition: "left .18s cubic-bezier(.2,.7,.3,1)",
          }}
        />
      </span>
    </button>
  );
}

/**
 * Render a text button in one of the shared variants.
 *
 * @param {object} props
 * @param {React.ReactNode} props.children - The button label.
 * @param {"primary" | "secondary" | "ghost" | "danger"} [props.variant] - The visual variant.
 * @param {"sm" | "md"} [props.size] - The button height.
 * @param {() => void} [props.onClick] - Runs when the button is pressed.
 * @param {React.ReactNode} [props.icon] - An optional leading icon.
 * @param {string} [props.kbd] - An optional keyboard shortcut hint.
 * @param {boolean} [props.disabled] - Whether the button is disabled.
 * @returns {JSX.Element} The button.
 * @example
 * <Button variant="primary" kbd="Ctrl S" onClick={save}>Save changes</Button>
 */
function Button({
  children,
  variant = "secondary",
  size = "md",
  onClick,
  icon,
  kbd,
  disabled,
  ...buttonProps
}) {
  const t = useTokens();
  const [hover, setHover] = React.useState(false);
  const variants = {
    primary: {
      bg: t.accent,
      color: t.accentOn,
      border: t.accent,
      hover: `color-mix(in srgb, ${t.accent} 88%, ${t.isDark ? "#FFFFFF" : "#000000"})`,
    },
    secondary: {
      bg: t.surface2,
      color: t.text,
      border: t.borderHi,
      hover: t.hover,
    },
    ghost: {
      bg: "transparent",
      color: t.textDim,
      border: "transparent",
      hover: t.hover,
    },
    danger: {
      bg: "transparent",
      color: t.dangerText,
      border: t.borderHi,
      hover: `color-mix(in srgb, ${t.dangerText} 12%, transparent)`,
    },
  };
  // A disabled button of any variant reads as inactive, not as a faded accent.
  const variantStyle = disabled
    ? { bg: t.surface2, color: t.textMuted, border: t.border, hover: t.surface2 }
    : variants[variant];
  const buttonHeight = size === "sm" ? 26 : 30;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      {...buttonProps}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        height: buttonHeight,
        padding: size === "sm" ? "0 10px" : "0 14px",
        background: hover && !disabled ? variantStyle.hover : variantStyle.bg,
        color: variantStyle.color,
        border: `1px solid ${variantStyle.border}`,
        borderRadius: t.radius,
        fontSize: size === "sm" ? 12.5 : 13,
        fontWeight: 700,
        fontFamily: "inherit",
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "background .12s, border-color .12s",
        display: "inline-flex",
        alignItems: "center",
        gap: kbd ? 8 : 6,
      }}
    >
      {icon}
      <span data-button-label="" style={CAP_BOX}>
        {children}
      </span>
      {kbd && <ShortcutHint keys={kbd} />}
    </button>
  );
}

/**
 * Render a hairline card with an optional titled header.
 *
 * @param {object} props
 * @param {string} [props.title] - The card heading.
 * @param {string} [props.subtitle] - A sentence under the heading.
 * @param {React.ReactNode} props.children - The card body.
 * @param {boolean} [props.padding] - Whether the body has horizontal padding.
 * @returns {JSX.Element} The card.
 * @example
 * <Card title="Startup"><Row label="Launch at login">...</Row></Card>
 */
function Card({ title, subtitle, children, padding = true }) {
  const t = useTokens();
  return (
    <section
      style={{
        background: t.surface,
        border: `1px solid ${t.border}`,
        borderRadius: t.radius,
        overflow: "hidden",
      }}
    >
      {(title || subtitle) && (
        <div
          style={{
            padding: `${t.rowPad - 2}px ${t.cardPad}px`,
            borderBottom: `1px solid ${t.border}`,
          }}
        >
          {title && (
            <h2
              style={{
                margin: 0,
                fontSize: 12.5,
                fontWeight: 700,
                color: t.text,
              }}
            >
              {title}
            </h2>
          )}
          {subtitle && (
            <div style={{ fontSize: 12.5, color: t.textDim, marginTop: 2, lineHeight: 1.45 }}>
              {subtitle}
            </div>
          )}
        </div>
      )}
      <div style={{ padding: padding ? `2px ${t.cardPad}px` : 0 }}>{children}</div>
    </section>
  );
}

/**
 * Render one labeled setting row and name its control for assistive technology.
 *
 * @param {object} props
 * @param {string} props.label - The setting name.
 * @param {string} [props.description] - A sentence that explains the setting.
 * @param {React.ReactNode} props.children - The control.
 * @param {boolean} [props.last] - Whether to omit the bottom divider.
 * @returns {JSX.Element} The row.
 */
function Row({ label, description, children, last }) {
  const t = useTokens();
  const labelId = React.useId();
  const descriptionId = React.useId();
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: `${t.rowPad}px 0`,
        borderBottom: last ? "none" : `1px solid ${t.border}`,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div id={labelId} style={{ fontSize: 13.5, fontWeight: 700, color: t.text }}>
          {label}
        </div>
        {description && (
          <div
            id={descriptionId}
            style={{
              fontSize: 12.5,
              color: t.textDim,
              marginTop: 2,
              lineHeight: 1.45,
            }}
          >
            {description}
          </div>
        )}
      </div>
      <RowControlContext.Provider
        value={{
          labelId,
          label,
          descriptionId: description ? descriptionId : undefined,
        }}
      >
        <div style={{ flexShrink: 0 }}>{children}</div>
      </RowControlContext.Provider>
    </div>
  );
}

/**
 * Render a single-choice segmented control as an arrow-key radio group.
 *
 * @param {object} props
 * @param {Array<string | {value: string, label: string}>} props.options - The choices.
 * @param {string} props.value - The selected value.
 * @param {(value: string) => void} props.onChange - Receives the chosen value.
 * @param {boolean} [props.mono] - Whether labels use the monospace face.
 * @returns {JSX.Element} The radio group.
 */
function Segmented({ options, value, onChange, mono }) {
  const t = useTokens();
  const row = React.useContext(RowControlContext);
  const optionRefs = React.useRef([]);
  const normalizedOptions = options.map((option) => ({
    value: typeof option === "object" ? option.value : option,
    label: typeof option === "object" ? option.label : option,
  }));
  const selectedIndex = normalizedOptions.findIndex((option) => option.value === value);

  const moveSelection = (event, index) => {
    let nextIndex;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (index + 1) % normalizedOptions.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (index - 1 + normalizedOptions.length) % normalizedOptions.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = normalizedOptions.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    onChange(normalizedOptions[nextIndex].value);
    optionRefs.current[nextIndex]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-labelledby={row?.labelId}
      aria-describedby={row?.descriptionId}
      style={{
        display: "inline-flex",
        background: t.surface2,
        border: `1px solid ${t.border}`,
        borderRadius: t.radius,
        padding: 2,
        gap: 2,
      }}
    >
      {normalizedOptions.map((option, index) => {
        const active = value === option.value;
        return (
          <button
            key={option.value}
            ref={(element) => {
              optionRefs.current[index] = element;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={index === (selectedIndex >= 0 ? selectedIndex : 0) ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => moveSelection(event, index)}
            style={{
              height: 24,
              padding: "0 10px",
              background: active ? t.accentBg : "transparent",
              border: `1px solid ${active ? t.accent : "transparent"}`,
              borderRadius: Math.max(0, t.radius - 1),
              color: active ? t.accent : t.textDim,
              fontSize: 12.5,
              fontWeight: 700,
              fontFamily: mono ? t.mono : "inherit",
              cursor: "pointer",
              transition: "background .12s, color .12s",
              // The options sit 2 px apart, so a ring drawn outside would
              // be covered by the next option.
              outlineOffset: -2,
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Return the shared style of the stepper buttons.
 *
 * @param {object} t - The design tokens.
 * @returns {object} The inline style.
 */
function stepButtonStyle(t) {
  return {
    width: 26,
    height: "100%",
    border: "none",
    background: "transparent",
    color: t.textDim,
    fontSize: 14,
    cursor: "pointer",
    fontFamily: "inherit",
    // The group clips overflow, so the focus ring is drawn inside the button.
    outlineOffset: -2,
  };
}

const STEPPER_HOLD_DELAY_MS = 400;
const STEPPER_REPEAT_MS = 60;

/**
 * Render a stepper button that repeats while held.
 *
 * @param {object} props
 * @param {string} props.label - The accessible name.
 * @param {-1 | 1} props.direction - The step direction.
 * @param {boolean} props.disabled - Whether the bound is reached.
 * @param {(direction: number) => void} props.onStep - Applies one step.
 * @param {object} props.tokens - The design tokens.
 * @returns {JSX.Element} The button.
 */
function StepperButton({ label, direction, disabled, onStep, tokens }) {
  const delayTimer = React.useRef(null);
  const repeatTimer = React.useRef(null);
  const suppressClick = React.useRef(false);

  const stopRepeating = React.useCallback(() => {
    if (delayTimer.current) clearTimeout(delayTimer.current);
    if (repeatTimer.current) clearInterval(repeatTimer.current);
    delayTimer.current = null;
    repeatTimer.current = null;
  }, []);

  React.useEffect(() => stopRepeating, [stopRepeating]);

  const onPointerDown = (event) => {
    if (event.button !== 0 || disabled) return;
    suppressClick.current = false;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    delayTimer.current = setTimeout(() => {
      suppressClick.current = true;
      onStep(direction);
      repeatTimer.current = setInterval(() => onStep(direction), STEPPER_REPEAT_MS);
    }, STEPPER_HOLD_DELAY_MS);
  };

  const onClick = () => {
    if (suppressClick.current) {
      suppressClick.current = false;
      return;
    }
    onStep(direction);
  };

  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      onPointerDown={onPointerDown}
      onPointerUp={stopRepeating}
      onPointerCancel={stopRepeating}
      onLostPointerCapture={stopRepeating}
      style={{
        ...stepButtonStyle(tokens),
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.45 : 1,
      }}
    >
      <Icon name={direction < 0 ? "minus" : "plus"} size={12} />
    </button>
  );
}

/**
 * Render a bounded number field with decrement and increment buttons.
 *
 * Typed values are committed on blur or Enter; the buttons repeat when held.
 *
 * @param {object} props
 * @param {number} props.value - The current value.
 * @param {(value: number) => void} props.onChange - Receives each committed value.
 * @param {number} [props.min] - The smallest allowed value.
 * @param {number} [props.max] - The largest allowed value.
 * @param {number} [props.step] - The increment.
 * @param {string} [props.suffix] - A unit shown after the number.
 * @returns {JSX.Element} The stepper.
 */
function Stepper({ value, onChange, min = 1, max = 9999, step = 1, suffix }) {
  const t = useTokens();
  const row = React.useContext(RowControlContext);
  const valueRef = React.useRef(value);
  const [editing, setEditing] = React.useState(false);
  const [draftValue, setDraftValue] = React.useState(String(value));
  valueRef.current = value;

  React.useEffect(() => {
    if (!editing) setDraftValue(String(value));
  }, [editing, value]);

  const publishValue = React.useCallback(
    (nextValue) => {
      valueRef.current = nextValue;
      setDraftValue(String(nextValue));
      onChange(nextValue);
    },
    [onChange],
  );

  const changeBy = React.useCallback(
    (direction) => {
      const nextValue = Math.max(min, Math.min(max, valueRef.current + direction * step));
      if (nextValue !== valueRef.current) publishValue(nextValue);
    },
    [max, min, publishValue, step],
  );

  const commitDraft = () => {
    const normalizedDraft = draftValue.trim();
    if (!normalizedDraft) {
      setDraftValue(String(valueRef.current));
      setEditing(false);
      return;
    }
    const parsed = Number(normalizedDraft);
    if (!Number.isFinite(parsed)) {
      setDraftValue(String(valueRef.current));
      setEditing(false);
      return;
    }
    const aligned = min + Math.round((parsed - min) / step) * step;
    const nextValue = Math.max(min, Math.min(max, aligned));
    if (nextValue !== valueRef.current) publishValue(nextValue);
    else setDraftValue(String(nextValue));
    setEditing(false);
  };

  return (
    <div
      role="group"
      aria-labelledby={row?.labelId}
      aria-describedby={row?.descriptionId}
      style={{
        display: "inline-flex",
        alignItems: "center",
        background: t.surface2,
        border: `1px solid ${t.borderHi}`,
        borderRadius: t.radius,
        height: 28,
        overflow: "hidden",
      }}
    >
      <StepperButton
        label={`Decrease ${row?.label || "value"}`}
        direction={-1}
        disabled={value <= min}
        onStep={changeBy}
        tokens={t}
      />
      {/* The label forwards clicks anywhere in the field to the input, which
          is only as wide as its digits so the value, or the value and its
          unit, can be centered as one group. */}
      <label
        className="fenestra-stepper-field"
        data-stepper-value=""
        data-focus-ring=""
        style={{
          minWidth: suffix ? 72 : 52,
          fontSize: 13,
          fontFamily: t.mono,
          color: t.text,
          fontVariantNumeric: "tabular-nums",
          borderLeft: `1px solid ${t.border}`,
          borderRight: `1px solid ${t.border}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: 4,
          padding: "0 6px",
          cursor: "text",
        }}
      >
        <input
          className="fenestra-stepper-input"
          type="number"
          min={min}
          max={max}
          step={step}
          value={draftValue}
          // A direct name keeps the field named while its row label is
          // scrolled out of view.
          aria-label={row?.label}
          aria-describedby={row?.descriptionId}
          aria-valuetext={suffix ? `${draftValue || valueRef.current} ${suffix}` : undefined}
          onFocus={(event) => {
            setEditing(true);
            event.currentTarget.select();
          }}
          onChange={(event) => setDraftValue(event.target.value)}
          onBlur={commitDraft}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              event.currentTarget.blur();
            } else if (event.key === "Escape") {
              event.preventDefault();
              setDraftValue(String(valueRef.current));
              setEditing(false);
              event.currentTarget.blur();
            }
          }}
          style={{
            // Monospaced digits are each 1ch wide; 1px on each side leaves
            // room for the caret.
            width: `calc(${Math.max(1, draftValue.length)}ch + 2px)`,
            height: "100%",
            padding: "0 1px",
            border: "none",
            background: "transparent",
            color: t.text,
            font: "inherit",
            textAlign: "center",
            fontVariantNumeric: "tabular-nums",
            appearance: "textfield",
          }}
        />
        {suffix && (
          <span aria-hidden="true" style={{ color: t.textMuted, fontSize: 11 }}>
            {suffix}
          </span>
        )}
      </label>
      <StepperButton
        label={`Increase ${row?.label || "value"}`}
        direction={1}
        disabled={value >= max}
        onStep={changeBy}
        tokens={t}
      />
    </div>
  );
}

/**
 * Render a keyboard- and pointer-operable percentage slider.
 *
 * @param {object} props
 * @param {number} props.value - The current value.
 * @param {(value: number) => void} props.onChange - Receives each new value.
 * @param {number} [props.min] - The smallest value.
 * @param {number} [props.max] - The largest value.
 * @returns {JSX.Element} The slider.
 */
function Slider({ value, onChange, min = 0, max = 100 }) {
  const t = useTokens();
  const row = React.useContext(RowControlContext);
  const percentage = ((value - min) / (max - min)) * 100;
  const sliderRef = React.useRef(null);
  const activePointer = React.useRef(null);
  const updateFromPointer = (event) => {
    const rect = sliderRef.current.getBoundingClientRect();
    onChange(
      Math.round(
        min + Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * (max - min),
      ),
    );
  };
  const onPointerDown = (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    activePointer.current = event.pointerId;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    updateFromPointer(event);
  };
  const onPointerMove = (event) => {
    if (activePointer.current === event.pointerId) updateFromPointer(event);
  };
  const finishPointer = (event) => {
    if (activePointer.current !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    activePointer.current = null;
  };
  const clamp = (nextValue) => Math.max(min, Math.min(max, nextValue));
  const onKeyDown = (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      event.preventDefault();
      onChange(clamp(value - 1));
    }
    if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      event.preventDefault();
      onChange(clamp(value + 1));
    }
    if (event.key === "Home") {
      event.preventDefault();
      onChange(min);
    }
    if (event.key === "End") {
      event.preventDefault();
      onChange(max);
    }
    if (event.key === "PageDown") {
      event.preventDefault();
      onChange(clamp(value - Math.max(1, Math.round((max - min) / 10))));
    }
    if (event.key === "PageUp") {
      event.preventDefault();
      onChange(clamp(value + Math.max(1, Math.round((max - min) / 10))));
    }
  };
  return (
    <div
      ref={sliderRef}
      role="slider"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-labelledby={row?.labelId}
      aria-describedby={row?.descriptionId}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finishPointer}
      onPointerCancel={finishPointer}
      style={{
        position: "relative",
        height: 24,
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        userSelect: "none",
        minWidth: 160,
      }}
    >
      <div
        style={{
          width: "100%",
          height: 4,
          borderRadius: 2,
          background: t.track,
          position: "relative",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            width: `${percentage}%`,
            background: t.accent,
            borderRadius: 2,
          }}
        />
      </div>
      <div
        style={{
          position: "absolute",
          left: `calc(${percentage}% - 8px)`,
          width: 16,
          height: 16,
          borderRadius: 8,
          background: t.surface,
          border: `2px solid ${t.accent}`,
        }}
      />
    </div>
  );
}

/**
 * Render a keycap for a key name or shortcut part.
 *
 * @param {object} props
 * @param {React.ReactNode} props.children - The key label.
 * @returns {JSX.Element} The keycap.
 */
function Kbd({ children }) {
  const t = useTokens();
  return (
    <span
      data-keycap=""
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        minWidth: 22,
        height: 20,
        padding: "0 6px",
        background: t.surface2,
        border: `1px solid ${t.borderHi}`,
        borderRadius: 3,
        color: t.text,
        fontSize: 11,
        fontFamily: t.mono,
        whiteSpace: "nowrap",
      }}
    >
      <span data-glyphs="" style={CAP_BOX}>
        {children}
      </span>
    </span>
  );
}

/**
 * Render a short status label.
 *
 * @param {object} props
 * @param {React.ReactNode} props.children - The label.
 * @param {"default" | "accent"} [props.tone] - The color treatment.
 * @returns {JSX.Element} The badge.
 */
function Badge({ children, tone = "default" }) {
  const t = useTokens();
  const tones = {
    default: { bg: t.surface2, color: t.textDim },
    accent: { bg: t.accentBg, color: t.accent },
  };
  const toneStyle = tones[tone];
  return (
    <span
      style={{
        fontSize: 11,
        padding: "1px 7px",
        borderRadius: 3,
        background: toneStyle.bg,
        color: toneStyle.color,
        fontWeight: 700,
        display: "inline-block",
      }}
    >
      {children}
    </span>
  );
}

export { CAP_BOX, Toggle, Button, ShortcutHint, Card, Row, Segmented, Stepper, Slider, Kbd, Badge };
