import { describe, it, expect, vi } from "vitest";
import { largestFittingSize } from "./fitText";

describe("largestFittingSize", () => {
  it("returns the maximum when the text already fits at full size", () => {
    expect(largestFittingSize(10, 40, () => true)).toBe(40);
  });

  it("does not shrink needlessly — one probe when it already fits", () => {
    const fits = vi.fn(() => true);
    largestFittingSize(10, 40, fits);
    expect(fits).toHaveBeenCalledTimes(1);
  });

  it("finds the largest size that fits", () => {
    // Anything at or below 23px fits.
    const result = largestFittingSize(10, 40, (px) => px <= 23);
    expect(result).toBeLessThanOrEqual(23);
    expect(result).toBeGreaterThan(22);
  });

  it("falls back to the minimum when nothing fits", () => {
    expect(largestFittingSize(10, 40, () => false)).toBe(10);
  });

  it("never returns a size outside the range", () => {
    for (const threshold of [5, 12, 25, 39, 60]) {
      const r = largestFittingSize(10, 40, (px) => px <= threshold);
      expect(r).toBeGreaterThanOrEqual(10);
      expect(r).toBeLessThanOrEqual(40);
    }
  });

  it("terminates on a degenerate range", () => {
    expect(largestFittingSize(20, 20, () => false)).toBe(20);
    expect(largestFittingSize(30, 10, () => true)).toBe(30);
  });

  it("converges within the step budget rather than looping", () => {
    const fits = vi.fn((px: number) => px <= 23);
    largestFittingSize(10, 40, fits);
    expect(fits.mock.calls.length).toBeLessThanOrEqual(25);
  });

  it("respects a tighter tolerance with a closer result", () => {
    const coarse = largestFittingSize(10, 40, (px) => px <= 23, 2);
    const fine = largestFittingSize(10, 40, (px) => px <= 23, 0.05);
    expect(23 - fine).toBeLessThanOrEqual(23 - coarse);
  });
});
