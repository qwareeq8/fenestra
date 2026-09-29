import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { FatalErrorScreen, FenestraErrorBoundary } from "../fatal-error.jsx";

function ThrowingChild() {
  throw new Error("Render failed.");
}

describe("FatalErrorScreen", () => {
  it("Provides an actionable fatal-error message.", () => {
    render(<FatalErrorScreen message="Fenestra could not load." />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Fenestra could not load. Restart Fenestra.",
    );
  });
});

describe("FenestraErrorBoundary", () => {
  it("Replaces a failed React tree with the fatal-error screen.", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(
        <FenestraErrorBoundary>
          <ThrowingChild />
        </FenestraErrorBoundary>,
      );

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Fenestra encountered an interface error.",
      );
      expect(consoleError).toHaveBeenCalled();
    } finally {
      consoleError.mockRestore();
    }
  });
});
