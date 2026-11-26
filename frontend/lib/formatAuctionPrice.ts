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

  // Avoid displaying a rounded "100.0 THOUSANDS" or "100.0 LAKHS".
  if (unit === "THOUSANDS" && scaledAmount >= 99.95) {
    divisor = 100_000;
    unit = "LAKHS";
    scaledAmount = absoluteAmount / divisor;
  } else if (unit === "LAKHS" && scaledAmount >= 99.95) {
    divisor = 10_000_000;
    unit = "CRORES";
    scaledAmount = absoluteAmount / divisor;
  }

  const rounded = scaledAmount.toFixed(1);
  const formatted = Number(rounded).toString();
  const sign = amount < 0 ? "-" : "";

  return {
    value: sign + formatted,
    unit,
  };
}
