import { describe, expect, it } from "vitest";
import { colorForValue, MISSING_COLOR } from "./color";

describe("color scale", () => {
  it("keeps missing values distinct from complete negative observations", () => {
    expect(colorForValue(null, "observed")).toBe(MISSING_COLOR);
    expect(colorForValue(Number.NaN, "estimate")).toBe(MISSING_COLOR);
    expect(colorForValue(0, "observed")).not.toBe(MISSING_COLOR);
    expect(colorForValue(1, "observed")).not.toBe(colorForValue(0, "observed"));
  });
  it("clamps probability endpoints and interpolates the middle", () => {
    expect(colorForValue(-2, "estimate")).toBe("#440154");
    expect(colorForValue(2, "estimate")).toBe("#fde725");
    expect(colorForValue(0.5, "estimate")).toBe("#21918c");
    expect(colorForValue(0.3, "estimate")).not.toBe(
      colorForValue(0.2, "estimate"),
    );
  });
  it("orders probability colors by relative luminance", () => {
    const luminance = (hex: string) => {
      const rgb = [1, 3, 5].map((offset) => {
        const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
        return value <= 0.04045
          ? value / 12.92
          : ((value + 0.055) / 1.055) ** 2.4;
      });
      return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
    };
    const values = Array.from({ length: 21 }, (_, i) =>
      luminance(colorForValue(i / 20, "estimate")),
    );
    values
      .slice(1)
      .forEach((value, i) => expect(value).toBeGreaterThan(values[i]));
  });
});
