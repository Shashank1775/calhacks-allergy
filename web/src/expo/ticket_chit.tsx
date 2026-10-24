/** One sent ticket on the expo rail, with its "start plate" button. */
import { allergen_name } from "../allergens";
import { Chit } from "../components/chit";
import type { Ticket } from "../types/TEMPORARY";
import { StartPlateButton } from "./start_plate_button";

export function TicketChit({ ticket }: { ticket: Ticket }) {
  return (
    <Chit label={`Ticket ${ticket.number}`}>
      <div className="chit_row bold">
        <span>TABLE {ticket.number}</span>
        <span>REV {ticket.revision}</span>
      </div>
      <hr className="dashed" />
      <p className="note">“{ticket.note}”</p>
      <p className="bold">FORBIDS: {ticket.forbidden.map(allergen_name).join(", ") || "nothing"}</p>
      <p className="muted">needs clean-confidence ≥ {ticket.pass_threshold}</p>
      <hr className="dashed" />
      <StartPlateButton ticket_id={ticket.ticket_id} />
    </Chit>
  );
}
