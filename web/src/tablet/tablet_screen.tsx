/** Tablet at the server station: new tickets, and bin setup at shift start. */
import { Rail } from "../components/rail";
import { BinSetup } from "./bin_setup";
import { TicketEntry } from "./ticket_entry";

export function TabletScreen() {
  return (
    <>
      <Rail title="New ticket" note="Type the guest's note. Answer every question. Send.">
        <TicketEntry />
      </Rail>
      <Rail title="Bin setup" note="At shift start: read each bin's label, check it, save it.">
        <BinSetup />
      </Rail>
    </>
  );
}
