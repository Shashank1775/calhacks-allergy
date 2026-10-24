/** Tablet at the server station: new tickets. Bin profiles are hard-coded by orders for the demo. */
import { Rail } from "../components/rail";
import { TicketEntry } from "./ticket_entry";

export function TabletScreen() {
  return (
    <Rail title="New ticket" note="Type the guest's note. Answer every question. Send.">
      <TicketEntry />
    </Rail>
  );
}
