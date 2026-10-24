/**
 * Asks vision to bind the plate at the plating spot to this ticket.
 * Rules: fail closed. A plate counts as bound only when vision says so.
 */
import { useState } from "react";
import { START_PLATE_ERRORS, start_plate } from "../api/vision";

export function StartPlateButton({ ticket_id }: { ticket_id: string }) {
  const [busy, set_busy] = useState(false);
  const [bound_plate, set_bound_plate] = useState<string | null>(null);
  const [error, set_error] = useState<string | null>(null);

  async function handle_click() {
    set_busy(true);
    set_error(null);
    const result = await start_plate(ticket_id);
    set_busy(false);
    if (result.ok) {
      set_bound_plate(result.data.plate_id);
    } else {
      set_bound_plate(null);
      set_error(START_PLATE_ERRORS[result.code] ?? `Couldn't start the plate: ${result.message}`);
    }
  }

  return (
    <div>
      <button type="button" className="btn" onClick={handle_click} disabled={busy}>
        {busy ? "STARTING…" : "START PLATE"}
      </button>
      {bound_plate && <p className="bold">Bound {bound_plate}.</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
