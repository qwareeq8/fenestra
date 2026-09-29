import { describe, expect, it } from "vitest";
import { createMockBridge, mockSettingsFromQuery, parseSettingsResult } from "../bridge.js";

describe("parseSettingsResult", () => {
  it("Returns settings from a successful backend response.", () => {
    expect(parseSettingsResult(JSON.stringify({ ok: true, data: { width_pct: 76 } }))).toEqual({
      width_pct: 76,
    });
  });

  it("Rejects a backend failure instead of returning writeable defaults.", () => {
    expect(() =>
      parseSettingsResult(JSON.stringify({ ok: false, error: "Settings read failed." })),
    ).toThrow("Settings read failed.");
  });

  it("Rejects a successful response without an object payload.", () => {
    expect(() => parseSettingsResult(JSON.stringify({ ok: true, data: null }))).toThrow(
      "The backend did not return settings data.",
    );
  });
});

describe("development mock bridge", () => {
  it("Reads a supported theme, accent, and density from the query string.", () => {
    expect(mockSettingsFromQuery("?theme=light&accent=teal&density=compact")).toEqual({
      theme: "light",
      accent: "teal",
      density: "compact",
    });
  });

  it("Ignores unsupported query values and unrelated parameters.", () => {
    expect(mockSettingsFromQuery("?theme=sepia&accent=Teal&density=&snap_key=f8")).toEqual({});
  });

  it("Starts in the requested appearance and keeps it as the saved value.", () => {
    const bridge = createMockBridge(mockSettingsFromQuery("?theme=light&density=compact"));
    let theme;
    bridge.get_theme_mode((raw) => (theme = JSON.parse(raw).data));
    const settings = parseSettingsResult(
      (() => {
        let raw;
        bridge.get_settings((value) => (raw = value));
        return raw;
      })(),
    );

    expect(theme).toEqual({ mode: "light", effective: "light" });
    expect(settings.density).toBe("compact");
    expect(settings.__fenestraSaved.density).toBe("compact");
  });

  it("Stages a draft, reports it dirty, and saves it on commit.", () => {
    const bridge = createMockBridge();
    const echoes = [];
    const dirty = [];
    bridge.settings_changed.connect((raw) => echoes.push(JSON.parse(raw)));
    bridge.dirty_changed.connect((value) => dirty.push(value));

    bridge.save_settings('{"enable_snap": false}', "stage-1", () => {});
    bridge.commit_draft("commit-1", () => {});

    expect(echoes[0]).toMatchObject({
      enable_snap: false,
      __fenestraSaved: { enable_snap: true },
      __fenestraTransaction: "stage-1",
    });
    expect(echoes[1].__fenestraSaved.enable_snap).toBe(false);
    expect(dirty).toEqual([true, false]);
  });

  it("Reports a failed test snap with an error tone.", () => {
    const bridge = createMockBridge();
    const statuses = [];
    let response;
    bridge.snap_status.connect((...args) => statuses.push(args));

    bridge.test_snap((raw) => (response = JSON.parse(raw)));

    expect(response.ok).toBe(false);
    expect(statuses[0][2]).toBe("error");
  });
});
