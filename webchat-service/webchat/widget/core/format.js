// Shared display formatting for trusted structured values.
export function moneyFromPence(value) {
  if (!Number.isInteger(value)) return null;
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    maximumFractionDigits: 0,
  }).format(value / 100);
}
