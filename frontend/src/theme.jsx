// Fenestra's design tokens and theme context. The palette, type, and density
// follow the shared desktop design language of SCI Map, Skopia, and Sella:
// near-black hairline surfaces, Computer Modern type, and one accent color.

import React from "react";

/**
 * Accent colors by name. Each entry holds the accent for text and fills on
 * dark and light surfaces and a quiet tinted fill for selected states. Every
 * accent keeps 4.5:1 contrast against its surfaces, its tinted fill, and the
 * ink drawn on it.
 */
const ACCENTS = {
  amber: { dark: "#E2A54A", light: "#8A5A0E", bgD: "#362E23", bgL: "#F3EEE7" },
  green: { dark: "#46B98A", light: "#1F6B4C", bgD: "#1D312E", bgL: "#E9F0ED" },
  teal: { dark: "#4FB8B0", light: "#1D6560", bgD: "#1E3134", bgL: "#E8F0EF" },
  blue: { dark: "#79A6F2", light: "#2A5BC0", bgD: "#252E3E", bgL: "#EAEFF9" },
  violet: { dark: "#A593FF", light: "#5B3FB8", bgD: "#2C2B40", bgL: "#EFECF8" },
};

/** Light-theme surfaces and text. */
const LIGHT = {
  bg: "#FAFBFC",
  surface: "#FFFFFF",
  surface2: "#F4F5F7",
  sidebar: "#F4F5F7",
  border: "#DDE0E6",
  borderHi: "#B6BCC4",
  text: "#1A1D23",
  textDim: "#5A5F68",
  textMuted: "#686D76",
  dangerText: "#A92F2F",
  warning: "#805504",
  hover: "rgba(15,23,42,0.05)",
  track: "#D8DCE2",
  switchOff: "#858B95",
  shadow: "none",
  overlay: "rgba(17,24,39,0.32)",
  dialogShadow: "0 12px 32px rgba(15,23,42,0.16)",
};

/** Dark-theme surfaces and text. */
const DARK = {
  bg: "#0E0F12",
  surface: "#15171C",
  surface2: "#1C1F26",
  sidebar: "#121418",
  border: "#262932",
  borderHi: "#3A414E",
  text: "#E6E8EC",
  textDim: "#9A9EA8",
  textMuted: "#868B95",
  dangerText: "#E87878",
  warning: "#D4A14D",
  hover: "rgba(255,255,255,0.05)",
  track: "#2C323D",
  switchOff: "#6B717C",
  shadow: "none",
  overlay: "rgba(0,0,0,0.55)",
  dialogShadow: "0 12px 32px rgba(0,0,0,0.5)",
};

/** Spacing and type sizes for each density preset. */
const DENSITIES = {
  compact: {
    rowPad: 9,
    sectionGap: 10,
    cardPad: 14,
    fontBase: 13,
    titleSize: 22,
  },
  cozy: {
    rowPad: 12,
    sectionGap: 14,
    cardPad: 18,
    fontBase: 13.5,
    titleSize: 24,
  },
  comfortable: {
    rowPad: 16,
    sectionGap: 18,
    cardPad: 22,
    fontBase: 14,
    titleSize: 26,
  },
};

/** Sans serif for interface chrome, backed by the bundled WebCM faces. */
const FONT = '"WebCM Sans 10", "CMU Sans Serif", "Segoe UI", system-ui, -apple-system, sans-serif';
/** Serif for page titles. */
const SERIF = '"WebCM Serif 10", "CMU Serif", Georgia, "Times New Roman", serif';
/** Monospace for keys, numbers, and shortcuts. */
const MONO = '"WebCM Mono 10", "CMU Typewriter Text", Consolas, "Cascadia Mono", monospace';

const ThemeCtx = React.createContext(null);

/**
 * Provide design tokens derived from the current tweaks.
 *
 * @param {object} props
 * @param {{theme: string, accent: string, density: string, radius: number}} props.tweaks
 *   The resolved theme, accent name, density name, and corner radius.
 * @param {(updates: object) => void} props.setTweaks - Merges tweak updates.
 * @param {React.ReactNode} props.children - The themed subtree.
 * @returns {JSX.Element} The context provider.
 * @example
 * <ThemeProvider tweaks={{ theme: "dark", accent: "amber", density: "cozy", radius: 4 }}
 *   setTweaks={update}>
 *   <App />
 * </ThemeProvider>
 */
function ThemeProvider({ tweaks, setTweaks, children }) {
  // Memoize tokens on the tweak values they depend on so the context value
  // stays referentially stable across unrelated renders.
  const tokens = React.useMemo(() => {
    const isDark = tweaks.theme === "dark";
    const palette = isDark ? DARK : LIGHT;
    const accentDef = ACCENTS[tweaks.accent] || ACCENTS.amber;
    const density = DENSITIES[tweaks.density] || DENSITIES.cozy;
    return {
      ...palette,
      accent: isDark ? accentDef.dark : accentDef.light,
      accentBg: isDark ? accentDef.bgD : accentDef.bgL,
      accentOn: isDark ? "#14100A" : "#FFFFFF",
      ...density,
      radius: tweaks.radius,
      font: FONT,
      serif: SERIF,
      mono: MONO,
      isDark,
    };
  }, [tweaks.theme, tweaks.accent, tweaks.density, tweaks.radius]);
  const api = React.useMemo(() => ({ tokens, tweaks, setTweaks }), [tokens, tweaks, setTweaks]);
  return <ThemeCtx.Provider value={api}>{children}</ThemeCtx.Provider>;
}

/**
 * Return the theme context.
 *
 * @returns {{tokens: object, tweaks: object, setTweaks: Function}} The context value.
 * @throws {Error} When called outside a ThemeProvider.
 */
function useTheme() {
  const context = React.useContext(ThemeCtx);
  if (!context) {
    throw new Error("useTheme must be used within ThemeProvider.");
  }
  return context;
}

/**
 * Return the current design tokens.
 *
 * @returns {object} Colors, spacing, radius, and font stacks.
 */
function useTokens() {
  return useTheme().tokens;
}

export { ThemeProvider, useTheme, useTokens, ACCENTS, LIGHT, DARK, DENSITIES, FONT, SERIF, MONO };
