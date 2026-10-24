/** Tablet at the server station: new tickets and amendments. Bin profiles are hard-coded by orders for the demo. */
import { Rail } from "../components/rail";
import { AmendTicket } from "./amend_ticket";
import { TicketEntry } from "./ticket_entry";

export function TabletScreen() {
  return (
    <>
      <Rail title="New ticket" note="Type the guest's note. Answer every question. Send.">
        <TicketEntry />
      </Rail>
      <Rail title="Change a ticket" note="A late change rechecks the whole plate.">
        <AmendTicket />
      </Rail>
    </>
  );
}
