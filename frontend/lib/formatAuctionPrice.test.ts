import { describe, expect, it } from "vitest";
import { formatAuctionPrice } from "./formatAuctionPrice";

describe("formatAuctionPrice", () => {
  it("formats rupee amounts below one thousand without scaling", () => {
    expect(formatAuctionPrice(500)).toEqual({ value: "500", unit: "RUPEES" });
  });

  it("formats thousands with a scaled value", () => {
    expect(formatAuctionPrice(25_000)).toEqual({ value: "25", unit: "THOUSANDS" });
  });

  it("formats lakhs with a scaled decimal value", () => {
    expect(formatAuctionPrice(1_020_000)).toEqual({ value: "10.2", unit: "LAKHS" });
  });

  it("formats crores with a scaled value", () => {
    expect(formatAuctionPrice(10_000_000)).toEqual({ value: "1", unit: "CRORES" });
  });

  it("promotes values that round into the next unit", () => {
    expect(formatAuctionPrice(9_999_999)).toEqual({ value: "1", unit: "CRORES" });
    expect(formatAuctionPrice(99_999)).toEqual({ value: "1", unit: "LAKHS" });
  });

  it("preserves a negative sign for display without changing magnitude", () => {
    expect(formatAuctionPrice(-1_020_000)).toEqual({ value: "-10.2", unit: "LAKHS" });
  });

  it("handles non-finite values safely", () => {
    expect(formatAuctionPrice(Number.NaN)).toEqual({ value: "0", unit: "RUPEES" });
  });
});
