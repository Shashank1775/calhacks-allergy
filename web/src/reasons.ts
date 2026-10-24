/**
 * Human labels for verdict reason codes (contracts/schemas/verdict.schema.json).
 * An unrecognized code still renders, labelled as unrecognized; a hold never loses a reason.
 */
export const REASON_LABELS: Record<string, string> = {
  forbidden_allergen: "A forbidden allergen can reach the plate",
  low_confidence: "Clean-confidence is below the ticket's threshold",
  unknown_history: "Something with unknown history touched the plate",
  stale_track: "Tracking went stale",
  missing_input: "An input was missing",
  error: "Something went wrong while checking",
};

export function reason_label(code: string): string {
  return REASON_LABELS[code] ?? `Unrecognized reason "${code}"`;
}
