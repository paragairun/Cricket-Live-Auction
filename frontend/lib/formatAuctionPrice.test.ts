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

  it("keeps up to two decimal places and trims trailing zeroes", () => {
    expect(formatAuctionPrice(1_235_000)).toEqual({ value: "12.35", unit: "LAKHS" });
    expect(formatAuctionPrice(1_250_000)).toEqual({ value: "12.5", unit: "LAKHS" });
    expect(formatAuctionPrice(1_200_000)).toEqual({ value: "12", unit: "LAKHS" });
  });

  it("formats crores with a scaled value", () => {
    expect(formatAuctionPrice(10_000_000)).toEqual({ value: "1", unit: "CRORES" });
  });

  it("promotes values that round into the next unit", () => {
    expect(formatAuctionPrice(9_999_999)).toEqual({ value: "1", unit: "CRORES" });
    expect(formatAuctionPrice(9_999_499)).toEqual({ value: "99.99", unit: "LAKHS" });
    expect(formatAuctionPrice(99_999)).toEqual({ value: "1", unit: "LAKHS" });
  });

  it("preserves a negative sign for display without changing magnitude", () => {
    expect(formatAuctionPrice(-1_020_000)).toEqual({ value: "-10.2", unit: "LAKHS" });
  });

  it("handles non-finite values safely", () => {
    expect(formatAuctionPrice(Number.NaN)).toEqual({ value: "0", unit: "RUPEES" });
  });
});
