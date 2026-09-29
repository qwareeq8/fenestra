/**
 * Render Fenestra in Chromium, capture every page and state, and fail on
 * geometry or accessibility defects.
 *
 * The script starts the Vite development server, which serves the interface
 * with the development mock bridge, and opens it with Playwright in each
 * theme, density, window size, and device scale factor. Every capture is
 * written to build/visual/ with one contact sheet per theme. Each capture must
 * pass these checks, which measure rendered glyphs rather than line boxes,
 * because the WebCM fonts' vertical metrics differ from system fonts:
 *
 * - Stepper values, or value and unit together, are centered in their field.
 * - Button labels share a centerline with their shortcut hints, and hint and
 *   keycap text is centered in its box.
 * - No text is clipped or overflows its container unless it shows an ellipsis.
 * - No two interactive elements overlap, and focus rings are fully visible.
 * - Interactive targets are at least 24 by 24 CSS pixels.
 * - axe-core finds no serious or critical violations, including contrast.
 *
 * The checks never compare pixels with stored images, because fonts rasterize
 * differently on each host.
 *
 * Usage: npm run visual:check [-- --update-docs] [-- --only=<text>]
 *
 * Set FENESTRA_CHROMIUM to a Chromium executable to use it instead of the
 * browser that `npx playwright install chromium` downloads. --update-docs
 * also copies the dark Window snap capture at device scale factor 2 to
 * docs/images/window-snap-dark.png, and --only=<text> limits the run to the
 * variants whose names contain the text, such as --only=light-compact.
 */

import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { chromium } from "playwright";
import { createServer } from "vite";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = path.resolve(FRONTEND, "..");
const OUTPUT = path.join(ROOT, "build", "visual");
const DOCS_IMAGE = path.join(ROOT, "docs", "images", "window-snap-dark.png");

const THEMES = ["dark", "light"];
const DENSITIES = ["compact", "cozy", "comfortable"];
const WINDOWS = [
  { width: 860, height: 600 },
  { width: 1000, height: 620 },
];
const SCALES = [1, 2];
const ACCENTS = ["amber", "green", "teal", "blue", "violet"];
const DEFAULT_ACCENT = "amber";
const DOCS_VARIANT = { theme: "dark", density: "cozy", width: 1000, height: 620, scale: 2 };

// Measured positions may differ from the ideal by at most this many CSS pixels.
const TOLERANCE_PX = 1;
const MIN_TARGET_PX = 24;
const AXE_BLOCKING_IMPACTS = new Set(["serious", "critical"]);

/**
 * Click a sidebar page and wait for its heading.
 *
 * @param {string} label - The page name in the sidebar.
 * @returns {(page: import("playwright").Page) => Promise<void>} The setup step.
 */
function openPage(label) {
  return async (page) => {
    await page
      .getByRole("navigation", { name: "Settings pages" })
      .getByRole("button", { name: label })
      .click();
    await page.getByRole("heading", { level: 1, name: label }).waitFor();
  };
}

/**
 * Press Tab until the element matching a selector has keyboard focus.
 *
 * @param {import("playwright").Page} page - The page.
 * @param {string} selector - A CSS selector for the target.
 */
async function focusWithTab(page, selector) {
  const target = page.locator(selector).first();
  await target.waitFor();
  for (let presses = 0; presses < 80; presses += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => element === document.activeElement)) return;
  }
  throw new Error(`Tab never reached ${selector}.`);
}

/** Every page and state the check captures, each set up from a fresh load. */
const CAPTURES = [
  { name: "snap-top", setup: async () => {} },
  {
    name: "snap-bottom",
    setup: async (page) => {
      await page.locator("main").evaluate((main) => {
        main.scrollTop = main.scrollHeight;
      });
    },
  },
  { name: "explorer", setup: openPage("Explorer") },
  { name: "shortcuts", setup: openPage("Shortcuts") },
  { name: "general", setup: openPage("General") },
  { name: "about", setup: openPage("About") },
  {
    name: "command-palette",
    setup: async (page) => {
      await page.keyboard.press("Control+K");
      await page.getByRole("combobox", { name: "Search commands" }).waitFor();
      await page.waitForFunction(() => document.activeElement?.getAttribute("role") === "combobox");
    },
  },
  {
    name: "reset-confirmation",
    setup: async (page) => {
      await openPage("General")(page);
      await page.getByRole("button", { name: "Reset", exact: true }).click();
      await page.getByRole("dialog", { name: "Reset all settings" }).waitFor();
    },
  },
  {
    name: "folder-view-confirmation",
    setup: async (page) => {
      await openPage("Explorer")(page);
      await page.getByRole("button", { name: "Make Details the default" }).click();
      await page.getByRole("dialog", { name: "Make Details the default?" }).waitFor();
    },
  },
  {
    name: "key-capture",
    setup: async (page) => {
      await page.getByRole("button", { name: "Snap key: SHIFT" }).click();
      await page.getByRole("button", { name: "Snap key: Press a key" }).waitFor();
    },
  },
  {
    name: "unsaved-changes",
    setup: async (page) => {
      await page.getByRole("switch").nth(1).click();
      await page.getByText("Unsaved changes").waitFor();
    },
    expect: async (page) =>
      (await page.getByRole("button", { name: "Save changes" }).isEnabled())
        ? []
        : ["Save changes: expected an enabled button with unsaved changes."],
  },
  {
    name: "footer-error",
    setup: async (page) => {
      await page.getByRole("button", { name: "Test snap" }).click();
      await page
        .getByRole("alert")
        .getByText(/Test snap failed/)
        .waitFor();
    },
  },
  {
    name: "save-disabled",
    setup: openPage("Shortcuts"),
    expect: async (page) =>
      (await page.getByRole("button", { name: "Save changes" }).isDisabled())
        ? []
        : ["Save changes: expected a disabled button without changes."],
  },
  {
    name: "focus-stepper",
    setup: (page) => focusWithTab(page, 'button[aria-label="Increase Press count"]'),
    focus: true,
  },
  {
    name: "focus-segmented",
    setup: async (page) => {
      await openPage("General")(page);
      await focusWithTab(page, '[role="radiogroup"][aria-labelledby] [role="radio"][tabindex="0"]');
    },
    focus: true,
  },
  {
    name: "focus-accent",
    setup: async (page) => {
      await openPage("General")(page);
      await focusWithTab(page, '[aria-label="Accent color"] [role="radio"][tabindex="0"]');
    },
    focus: true,
  },
  {
    name: "focus-switch",
    setup: (page) => focusWithTab(page, '[role="switch"]'),
    focus: true,
  },
  {
    name: "focus-window-control",
    setup: (page) => focusWithTab(page, 'button[aria-label="Close Fenestra"]'),
    focus: true,
  },
  {
    name: "focus-navigation",
    setup: async (page) => {
      await openPage("Shortcuts")(page);
      await focusWithTab(page, 'nav[aria-label="Settings pages"] button:has-text("Explorer")');
    },
    focus: true,
  },
];

// Captures repeated for every accent, so contrast is checked for each one.
const ACCENT_CAPTURES = new Set(["snap-top", "general", "command-palette"]);

/**
 * Measure the rendered interface and return every geometry problem.
 *
 * Runs in the page. The screenshot of the current viewport arrives as a
 * base64 PNG, and glyph boxes come from its pixels: a glyph box spans the
 * ink horizontally and runs from the top of the ink to the text baseline, so
 * descenders do not move a centered label.
 *
 * @param {{png: string, tolerance: number, minTarget: number, expectFocus: boolean}} options
 * @returns {Promise<string[]>} Problems, each naming the element and the measurements.
 */
async function measureInPage({ png, tolerance, minTarget, expectFocus }) {
  const problems = [];
  const bytes = Uint8Array.from(atob(png), (character) => character.charCodeAt(0));
  const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0);
  const image = context.getImageData(0, 0, bitmap.width, bitmap.height);
  const scale = bitmap.width / window.innerWidth;
  const round = (value) => Math.round(value * 100) / 100;
  const fmt = (box) =>
    `left ${round(box.left)}, top ${round(box.top)}, right ${round(box.right)}, bottom ${round(box.bottom)}`;

  const describe = (element) => {
    const role = element.getAttribute("role");
    const labelledBy = (element.getAttribute("aria-labelledby") ?? "")
      .split(/\s+/)
      .map((id) => id && document.getElementById(id)?.textContent?.trim())
      .filter(Boolean)
      .join(" ");
    const name =
      element.getAttribute("aria-label") ||
      labelledBy ||
      element.textContent?.trim().replace(/\s+/g, " ").slice(0, 40) ||
      element.tagName.toLowerCase();
    return `${role || element.tagName.toLowerCase()} "${name}"`;
  };

  const isRendered = (element) => {
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden") return false;
      if (Number(style.opacity) === 0) return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  // The padding box of an element, where its overflow is clipped.
  const paddingBox = (element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      left: rect.left + parseFloat(style.borderLeftWidth),
      top: rect.top + parseFloat(style.borderTopWidth),
      right: rect.right - parseFloat(style.borderRightWidth),
      bottom: rect.bottom - parseFloat(style.borderBottomWidth),
    };
  };

  const intersect = (a, b) => ({
    left: Math.max(a.left, b.left),
    top: Math.max(a.top, b.top),
    right: Math.min(a.right, b.right),
    bottom: Math.min(a.bottom, b.bottom),
  });

  const viewport = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };

  // The part of an element that its clipping ancestors and the viewport show.
  const visibleBox = (element) => {
    let box = intersect(element.getBoundingClientRect(), viewport);
    for (let node = element.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.overflowX !== "visible" || style.overflowY !== "visible") {
        box = intersect(box, paddingBox(node));
      }
    }
    return box;
  };
  const isEmpty = (box) => box.right - box.left <= 0.5 || box.bottom - box.top <= 0.5;
  const contains = (outer, inner) =>
    inner.left >= outer.left - 0.01 &&
    inner.top >= outer.top - 0.01 &&
    inner.right <= outer.right + 0.01 &&
    inner.bottom <= outer.bottom + 0.01;

  // The baseline of the first line of text inside an element.
  const baselineOf = (element) => {
    const probe = document.createElement("span");
    probe.style.cssText =
      "display:inline-block;width:0;height:0;margin:0;padding:0;border:0;vertical-align:baseline";
    element.appendChild(probe);
    const baseline = probe.getBoundingClientRect().top;
    probe.remove();
    return baseline;
  };

  // The box of the ink inside a region of the screenshot, in CSS pixels.
  const inkBox = (region) => {
    const x0 = Math.max(0, Math.ceil(region.left * scale));
    const y0 = Math.max(0, Math.ceil(region.top * scale));
    const x1 = Math.min(image.width, Math.floor(region.right * scale));
    const y1 = Math.min(image.height, Math.floor(region.bottom * scale));
    if (x1 <= x0 || y1 <= y0) return null;
    const { data, width } = image;
    const counts = new Map();
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        const offset = (y * width + x) * 4;
        const key = (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2];
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
    }
    let background = 0;
    let most = -1;
    for (const [key, count] of counts) {
      if (count > most) {
        most = count;
        background = key;
      }
    }
    const br = (background >> 16) & 255;
    const bg = (background >> 8) & 255;
    const bb = background & 255;
    const difference = (offset) =>
      Math.max(
        Math.abs(data[offset] - br),
        Math.abs(data[offset + 1] - bg),
        Math.abs(data[offset + 2] - bb),
      );
    let strongest = 0;
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        strongest = Math.max(strongest, difference((y * width + x) * 4));
      }
    }
    if (strongest < 24) return null;
    const threshold = strongest * 0.4;
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        if (difference((y * width + x) * 4) >= threshold) {
          left = Math.min(left, x);
          right = Math.max(right, x + 1);
          top = Math.min(top, y);
          bottom = Math.max(bottom, y + 1);
        }
      }
    }
    return { left: left / scale, top: top / scale, right: right / scale, bottom: bottom / scale };
  };

  const inset = (box, amount) => ({
    left: box.left + amount,
    top: box.top + amount,
    right: box.right - amount,
    bottom: box.bottom - amount,
  });
  const centerX = (box) => (box.left + box.right) / 2;
  const centerY = (box) => (box.top + box.bottom) / 2;
  const modal = document.querySelector('[aria-modal="true"]');
  // An element counts only when nothing clips it and no modal covers it.
  const fullyShown = (element) => {
    if (modal && !modal.contains(element)) return false;
    const rect = element.getBoundingClientRect();
    return contains(visibleBox(element), rect);
  };
  // Keycaps and hints are flex containers, whose children do not sit on a
  // baseline, so their text must be wrapped in a [data-glyphs] element.
  const glyphText = (container, kind, name) => {
    const text = container.querySelector("[data-glyphs]");
    if (!text) problems.push(`${kind} ${name}: its text has no [data-glyphs] wrapper to measure.`);
    return text;
  };
  const glyphBox = (textElement, region) => {
    const ink = inkBox(region);
    if (!ink) return null;
    return { left: ink.left, right: ink.right, top: ink.top, bottom: baselineOf(textElement) };
  };

  // Stepper values, alone or with their unit, are centered in the field.
  for (const field of document.querySelectorAll("[data-stepper-value]")) {
    if (!isRendered(field) || !fullyShown(field)) continue;
    const box = paddingBox(field);
    const ink = inkBox(inset(box, 1));
    const value = [field.querySelector("input")?.value, field.textContent.trim()]
      .filter(Boolean)
      .join(" ");
    if (!ink) {
      problems.push(`Stepper value ${JSON.stringify(value)}: no glyphs were found.`);
      continue;
    }
    const offset = centerX(ink) - centerX(box);
    if (Math.abs(offset) > tolerance) {
      problems.push(
        `Stepper value ${JSON.stringify(value)}: ` +
          `glyphs span ${round(ink.left)} to ${round(ink.right)} in a field spanning ` +
          `${round(box.left)} to ${round(box.right)}, off center by ${round(offset)} px.`,
      );
    }
  }

  // Shortcut hints share their button label's centerline and center their text.
  for (const hint of document.querySelectorAll("[data-shortcut-hint]")) {
    if (!isRendered(hint) || !fullyShown(hint)) continue;
    const box = hint.getBoundingClientRect();
    const name = JSON.stringify(hint.textContent.trim());
    const text = glyphText(hint, "Shortcut hint", name);
    if (!text) continue;
    const glyphs = glyphBox(text, inset(paddingBox(hint), 0.5));
    if (!glyphs) {
      problems.push(`Shortcut hint ${name}: no glyphs were found.`);
      continue;
    }
    const dx = centerX(glyphs) - centerX(box);
    const dy = centerY(glyphs) - centerY(box);
    if (Math.abs(dx) > tolerance || Math.abs(dy) > tolerance) {
      problems.push(
        `Shortcut hint ${name}: glyphs (${fmt(glyphs)}) are off center in their box ` +
          `(${fmt(box)}) by ${round(dx)} px horizontally and ${round(dy)} px vertically.`,
      );
    }
    const owner = hint.closest("button");
    const label = owner?.querySelector("[data-button-label]");
    if (!label) {
      problems.push(`Shortcut hint ${name}: its button has no data-button-label element.`);
      continue;
    }
    const labelRect = label.getBoundingClientRect();
    const labelGlyphs = glyphBox(label, {
      left: labelRect.left - 1,
      right: labelRect.right + 1,
      top: labelRect.top - 2,
      bottom: labelRect.bottom + 2,
    });
    if (!labelGlyphs) {
      problems.push(`Button ${describe(owner)}: no label glyphs were found.`);
      continue;
    }
    const between = centerY(labelGlyphs) - centerY(glyphs);
    if (Math.abs(between) > tolerance) {
      problems.push(
        `Button ${describe(owner)}: the label's glyph center is at ${round(centerY(labelGlyphs))} ` +
          `and the hint's at ${round(centerY(glyphs))}, ${round(between)} px apart.`,
      );
    }
  }

  // Keycap text is centered in its keycap.
  for (const keycap of document.querySelectorAll("[data-keycap]")) {
    if (!isRendered(keycap) || !fullyShown(keycap)) continue;
    const box = keycap.getBoundingClientRect();
    const name = JSON.stringify(keycap.textContent.trim());
    const text = glyphText(keycap, "Keycap", name);
    if (!text) continue;
    const glyphs = glyphBox(text, inset(paddingBox(keycap), 0.5));
    if (!glyphs) {
      problems.push(`Keycap ${name}: no glyphs were found.`);
      continue;
    }
    const dx = centerX(glyphs) - centerX(box);
    const dy = centerY(glyphs) - centerY(box);
    if (Math.abs(dx) > tolerance || Math.abs(dy) > tolerance) {
      problems.push(
        `Keycap ${name}: glyphs (${fmt(glyphs)}) are off center in the keycap (${fmt(box)}) ` +
          `by ${round(dx)} px horizontally and ${round(dy)} px vertically.`,
      );
    }
  }

  // Text is neither clipped nor spilling out of its element, unless it shows
  // an intended ellipsis.
  const showsEllipsis = (element) => {
    for (let node = element; node && node !== document.body; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.textOverflow === "ellipsis" && style.overflowX !== "visible") return true;
    }
    return false;
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim()) continue;
    const element = node.parentElement;
    if (!element || !isRendered(element) || showsEllipsis(element)) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = [...range.getClientRects()].filter((rect) => rect.width > 0);
    if (rects.length === 0) continue;
    const text = {
      left: Math.min(...rects.map((rect) => rect.left)),
      top: Math.min(...rects.map((rect) => rect.top)),
      right: Math.max(...rects.map((rect) => rect.right)),
      bottom: Math.max(...rects.map((rect) => rect.bottom)),
    };
    const label = JSON.stringify(node.textContent.trim().replace(/\s+/g, " ").slice(0, 48));
    const own = element.getBoundingClientRect();
    // A box trimmed to the capital height and baseline is meant to be
    // shorter than the font's line box, so only its width is compared.
    const trimmed = (getComputedStyle(element).textBoxTrim ?? "none") !== "none";
    if (
      text.left < own.left - 0.5 ||
      text.right > own.right + 0.5 ||
      (!trimmed && (text.top < own.top - 0.5 || text.bottom > own.bottom + 0.5))
    ) {
      problems.push(`Text ${label} (${fmt(text)}) overflows its element (${fmt(own)}).`);
      continue;
    }
    // Content scrolled out of a scroll container is not clipped text, so an
    // axis stops being checked above the first container that scrolls it.
    let checkX = true;
    let checkY = true;
    for (
      let ancestor = element;
      ancestor && (checkX || checkY);
      ancestor = ancestor.parentElement
    ) {
      const style = getComputedStyle(ancestor);
      const scrollsX = style.overflowX === "auto" || style.overflowX === "scroll";
      const scrollsY = style.overflowY === "auto" || style.overflowY === "scroll";
      const clipsX = checkX && style.overflowX !== "visible";
      const clipsY = checkY && !scrollsY && style.overflowY !== "visible";
      if (scrollsY) checkY = false;
      if (!clipsX && !clipsY) continue;
      const clip = paddingBox(ancestor);
      const cutX = clipsX && (text.left < clip.left - 0.5 || text.right > clip.right + 0.5);
      const cutY = clipsY && (text.top < clip.top - 0.5 || text.bottom > clip.bottom + 0.5);
      if (cutX || cutY) {
        problems.push(
          `Text ${label} (${fmt(text)}) is clipped by ${describe(ancestor)} (${fmt(clip)}).`,
        );
        break;
      }
      if (scrollsX) checkX = false;
    }
  }
  for (const input of document.querySelectorAll("input")) {
    if (isRendered(input) && input.scrollWidth > input.clientWidth + 1) {
      problems.push(
        `Input ${describe(input)} with value ${JSON.stringify(input.value)} is clipped: ` +
          `its text needs ${input.scrollWidth} px of ${input.clientWidth} px.`,
      );
    }
  }

  // Interactive elements do not overlap and are large enough to hit.
  const interactiveSelector =
    'button, input, select, textarea, a[href], [role="switch"], [role="radio"], ' +
    '[role="slider"], [role="option"], [tabindex]:not([tabindex="-1"])';
  const interactive = [...document.querySelectorAll(interactiveSelector)].filter(
    (element) => isRendered(element) && (!modal || modal.contains(element)),
  );
  const shown = interactive
    .map((element) => ({ element, box: visibleBox(element) }))
    .filter((entry) => !isEmpty(entry.box));
  for (let first = 0; first < shown.length; first += 1) {
    for (let second = first + 1; second < shown.length; second += 1) {
      const a = shown[first];
      const b = shown[second];
      if (a.element.contains(b.element) || b.element.contains(a.element)) continue;
      const overlap = intersect(a.box, b.box);
      if (!isEmpty(overlap)) {
        problems.push(
          `${describe(a.element)} (${fmt(a.box)}) overlaps ${describe(b.element)} (${fmt(b.box)}).`,
        );
      }
    }
  }
  for (const element of interactive) {
    if (element.disabled) continue;
    // A label forwards its clicks to its input, so the label is the target.
    const target = (element.tagName === "INPUT" && element.closest("label")) || element;
    const rect = target.getBoundingClientRect();
    if (rect.width < minTarget - 0.01 || rect.height < minTarget - 0.01) {
      problems.push(
        `${describe(element)} is a ${round(rect.width)} by ${round(rect.height)} px target, ` +
          `smaller than ${minTarget} by ${minTarget} px.`,
      );
    }
  }

  // A focus ring is drawn and no clipping ancestor or window edge hides it.
  const focused = document.activeElement;
  const hasVisibleFocus = focused && focused !== document.body && focused.matches(":focus-visible");
  if (expectFocus && !hasVisibleFocus) {
    problems.push(`Keyboard focus: expected a :focus-visible element, found ${focused?.tagName}.`);
  }
  if (hasVisibleFocus) {
    // A field may draw the ring for a narrower input inside it.
    const ringOwner =
      getComputedStyle(focused).outlineStyle === "none"
        ? (focused.closest("[data-focus-ring]") ?? focused)
        : focused;
    const style = getComputedStyle(ringOwner);
    const width = parseFloat(style.outlineWidth);
    if (style.outlineStyle === "none" || !(width >= 1)) {
      problems.push(`Focus on ${describe(focused)} draws no outline.`);
    } else {
      const reach = parseFloat(style.outlineOffset) + width;
      const rect = ringOwner.getBoundingClientRect();
      const ring = {
        left: rect.left - reach,
        top: rect.top - reach,
        right: rect.right + reach,
        bottom: rect.bottom + reach,
      };
      if (!contains(viewport, ring)) {
        problems.push(`Focus ring of ${describe(focused)} (${fmt(ring)}) leaves the window.`);
      }
      for (let node = ringOwner.parentElement; node; node = node.parentElement) {
        const nodeStyle = getComputedStyle(node);
        if (nodeStyle.overflowX === "visible" && nodeStyle.overflowY === "visible") continue;
        const clip = paddingBox(node);
        if (!contains(clip, ring)) {
          problems.push(
            `Focus ring of ${describe(focused)} (${fmt(ring)}) is clipped by ` +
              `${describe(node)} with overflow ${nodeStyle.overflowX}/${nodeStyle.overflowY} (${fmt(clip)}).`,
          );
          break;
        }
      }
      // A neighboring control can paint over a ring drawn outside the element.
      for (const { element, box } of reach > 0 ? shown : []) {
        if (element.contains(ringOwner) || ringOwner.contains(element)) continue;
        if (!isEmpty(intersect(box, ring))) {
          problems.push(
            `Focus ring of ${describe(focused)} (${fmt(ring)}) is covered by ` +
              `${describe(element)} (${fmt(box)}).`,
          );
        }
      }
    }
  }

  return problems;
}

/**
 * Load the interface and wait until its fonts and first page are ready.
 *
 * @param {import("playwright").Page} page - The page.
 * @param {string} url - The development server URL with query parameters.
 */
async function loadInterface(page, url) {
  await page.goto(url);
  await page.getByRole("heading", { level: 1, name: "Window snap" }).waitFor();
  await page.evaluate(() => document.fonts.ready);
}

/** Wait two animation frames so layout and paint settle. */
async function settle(page) {
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
}

/**
 * Capture one state, run every check on it, and return the problems.
 *
 * @returns {Promise<{file: string, problems: string[]}>} The PNG path and problems.
 */
async function runCapture(page, baseUrl, variant, capture, { axe }) {
  const query = new URLSearchParams({
    theme: variant.theme,
    accent: variant.accent,
    density: variant.density,
  });
  await loadInterface(page, `${baseUrl}?${query}`);
  await capture.setup(page);
  // Keep the pointer off every control so no hover style shows.
  await page.mouse.move(1, 1);
  await settle(page);
  const file = path.join(OUTPUT, `${variant.id}-${capture.name}.png`);
  const png = await page.screenshot({ path: file, animations: "disabled", caret: "hide" });
  const problems = await page.evaluate(measureInPage, {
    png: png.toString("base64"),
    tolerance: TOLERANCE_PX,
    minTarget: MIN_TARGET_PX,
    expectFocus: Boolean(capture.focus),
  });
  if (capture.expect) problems.push(...(await capture.expect(page)));
  if (axe) {
    const results = await new AxeBuilder({ page }).analyze();
    for (const violation of results.violations) {
      if (!AXE_BLOCKING_IMPACTS.has(violation.impact)) continue;
      for (const node of violation.nodes) {
        problems.push(
          `axe ${violation.id} (${violation.impact}) at ${node.target.join(" ")}: ` +
            `${node.failureSummary?.replace(/\s+/g, " ").trim()}`,
        );
      }
    }
  }
  return { file, problems };
}

/** Write one contact sheet per theme from the scale-factor-1 captures. */
async function writeContactSheets(browser, sheets) {
  const page = await browser.newPage({ viewport: { width: 1800, height: 1000 } });
  const written = [];
  for (const [theme, rows] of sheets) {
    const columns = [...new Set(rows.flatMap((row) => row.cells.map((cell) => cell.column)))];
    const background = theme === "dark" ? "#0e0f12" : "#fafbfc";
    const ink = theme === "dark" ? "#e6e8ec" : "#1a1d23";
    const html = `<!doctype html><meta charset="utf-8"><title>Fenestra ${theme} captures</title>
<style>
  body { margin: 0; padding: 24px; background: ${background}; color: ${ink};
         font: 13px system-ui, sans-serif; }
  h1 { font-size: 18px; margin: 0 0 16px; }
  table { border-collapse: separate; border-spacing: 12px 10px; }
  th { font-weight: 600; text-align: left; white-space: nowrap; }
  img { display: block; width: 260px; border: 1px solid #888; }
</style>
<h1>Fenestra, ${theme} theme, device scale factor 1</h1>
<table>
  <tr><th></th>${columns.map((column) => `<th>${column}</th>`).join("")}</tr>
  ${rows
    .map(
      (row) =>
        `<tr><th>${row.name}</th>${columns
          .map((column) => {
            const cell = row.cells.find((candidate) => candidate.column === column);
            return `<td>${cell ? `<img src="${pathToFileURL(cell.file)}">` : ""}</td>`;
          })
          .join("")}</tr>`,
    )
    .join("\n  ")}
</table>`;
    const sheetHtml = path.join(OUTPUT, `contact-sheet-${theme}.html`);
    const sheetPng = path.join(OUTPUT, `contact-sheet-${theme}.png`);
    await writeFile(sheetHtml, html);
    await page.goto(pathToFileURL(sheetHtml).href);
    await page.evaluate(() =>
      Promise.all([...document.images].map((image) => image.decode().catch(() => undefined))),
    );
    await page.screenshot({ path: sheetPng, fullPage: true });
    written.push(sheetPng);
  }
  await page.close();
  return written;
}

/** Build the list of variants: the full matrix plus one row per accent. */
function variants() {
  const list = [];
  for (const theme of THEMES) {
    for (const density of DENSITIES) {
      for (const { width, height } of WINDOWS) {
        for (const scale of SCALES) {
          list.push({ theme, density, width, height, scale, accent: DEFAULT_ACCENT });
        }
      }
    }
    for (const accent of ACCENTS.filter((name) => name !== DEFAULT_ACCENT)) {
      list.push({ theme, density: "cozy", width: 1000, height: 620, scale: 1, accent });
    }
  }
  return list.map((variant) => ({
    ...variant,
    id:
      `${variant.theme}-${variant.density}-${variant.width}x${variant.height}@${variant.scale}x` +
      (variant.accent === DEFAULT_ACCENT ? "" : `-${variant.accent}`),
  }));
}

async function main() {
  const updateDocs = process.argv.includes("--update-docs");
  const only = process.argv.find((argument) => argument.startsWith("--only="))?.slice(7);
  const config = await readFile(path.join(ROOT, "fenestra", "app", "config.py"), "utf8");
  const version = /^APP_VERSION = "([^"]+)"$/m.exec(config)?.[1];
  if (!version) throw new Error("APP_VERSION was not found in fenestra/app/config.py.");
  process.env.VITE_APP_VERSION = version;

  await rm(OUTPUT, { recursive: true, force: true });
  await mkdir(OUTPUT, { recursive: true });

  const server = await createServer({
    root: FRONTEND,
    configFile: path.join(FRONTEND, "vite.config.js"),
    logLevel: "error",
    server: { host: "127.0.0.1", port: 5199, strictPort: false, hmr: false },
  });
  await server.listen();
  const baseUrl = server.resolvedUrls.local[0];
  const browser = await chromium.launch({
    executablePath: process.env.FENESTRA_CHROMIUM || undefined,
  });

  const failures = [];
  const sheets = new Map(THEMES.map((theme) => [theme, new Map()]));
  let captured = 0;
  try {
    // Let Vite optimize its dependencies before any capture, so a reload
    // cannot interrupt a measurement.
    const warmup = await browser.newPage();
    await loadInterface(warmup, baseUrl);
    await warmup.waitForTimeout(1000);
    await warmup.close();

    for (const variant of variants().filter((entry) => !only || entry.id.includes(only))) {
      const context = await browser.newContext({
        viewport: { width: variant.width, height: variant.height },
        deviceScaleFactor: variant.scale,
        colorScheme: variant.theme,
        reducedMotion: "reduce",
      });
      const page = await context.newPage();
      page.on("pageerror", (error) => failures.push(`${variant.id}: page error: ${error.message}`));
      const captures =
        variant.accent === DEFAULT_ACCENT
          ? CAPTURES
          : CAPTURES.filter((capture) => ACCENT_CAPTURES.has(capture.name));
      for (const capture of captures) {
        const { file, problems } = await runCapture(page, baseUrl, variant, capture, {
          axe: variant.scale === 1,
        });
        captured += 1;
        for (const problem of problems) failures.push(`${variant.id} ${capture.name}: ${problem}`);
        if (variant.scale === 1) {
          const rows = sheets.get(variant.theme);
          if (!rows.has(capture.name)) rows.set(capture.name, { name: capture.name, cells: [] });
          const column =
            variant.accent === DEFAULT_ACCENT
              ? `${variant.density} ${variant.width}x${variant.height}`
              : `${variant.accent} cozy`;
          rows.get(capture.name).cells.push({ column, file });
        }
        const docs = DOCS_VARIANT;
        if (
          updateDocs &&
          capture.name === "snap-top" &&
          variant.accent === DEFAULT_ACCENT &&
          variant.theme === docs.theme &&
          variant.density === docs.density &&
          variant.width === docs.width &&
          variant.height === docs.height &&
          variant.scale === docs.scale
        ) {
          await copyFile(file, DOCS_IMAGE);
        }
      }
      await context.close();
    }
    const written = await writeContactSheets(
      browser,
      [...sheets].map(([theme, rows]) => [theme, [...rows.values()]]),
    );
    console.log(`[visual] Captured ${captured} states in ${path.relative(ROOT, OUTPUT)}.`);
    for (const sheet of written)
      console.log(`[visual] Contact sheet: ${path.relative(ROOT, sheet)}.`);
  } finally {
    await browser.close();
    await server.close();
  }

  if (failures.length > 0) {
    console.error(`[visual] ${failures.length} problems:`);
    for (const failure of failures) console.error(`  ${failure}`);
    process.exitCode = 1;
    return;
  }
  console.log("[visual] OK: every geometry and accessibility check passed.");
}

await main();
