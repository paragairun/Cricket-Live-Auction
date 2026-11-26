export interface FormattedAuctionPrice {
  value: string;
  unit: string;
}

/**
 * Format a rupee amount using Indian number units for the auction price banner.
 * The underlying bid remains an integer rupee amount; this is display-only.
 */
export function formatAuctionPrice(amount: number): FormattedAuctionPrice {
  if (!Number.isFinite(amount)) {
    return { value: "0", unit: "RUPEES" };
  }

  const absoluteAmount = Math.abs(amount);
  let divisor = 1;
  let unit = "RUPEES";

  if (absoluteAmount >= 10_000_000) {
    divisor = 10_000_000;
    unit = "CRORES";
  } else if (absoluteAmount >= 100_000) {
    divisor = 100_000;
    unit = "LAKHS";
  } else if (absoluteAmount >= 1_000) {
    divisor = 1_000;
    unit = "THOUSANDS";
  }

  let scaledAmount = absoluteAmount / divisor;
  let roundedAmount = Number(scaledAmount.toFixed(2));

  // Promote a value when two-decimal rounding would display 100.00 units.
  if (unit === "THOUSANDS" && roundedAmount >= 100) {
    divisor = 100_000;
    unit = "LAKHS";
    scaledAmount = absoluteAmount / divisor;
    roundedAmount = Number(scaledAmount.toFixed(2));
  } else if (unit === "LAKHS" && roundedAmount >= 100) {
    divisor = 10_000_000;
    unit = "CRORES";
    scaledAmount = absoluteAmount / divisor;
    roundedAmount = Number(scaledAmount.toFixed(2));
  }

  // Number conversion removes insignificant trailing zeroes:
  // 12.50 -> 12.5, 12.00 -> 12, while 12.35 remains 12.35.
  const formatted = roundedAmount.toString();
  const sign = amount < 0 ? "-" : "";

  return {
    value: sign + formatted,
    unit,
  };
}
