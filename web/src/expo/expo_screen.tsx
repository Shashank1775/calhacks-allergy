/**
 * Expo board: tickets on the rail, plates on the pass, and why a plate is held.
 *
 * Reads: ticket, plate_binding, verdict, zone, entity, entity_state (state source); vision health.
 * Rules: every badge comes from plate_badge(), and the gate panel's state from gate_view(); this
 * screen decides nothing.
 */
import { Rail } from "../components/rail";
import { useFeedStatus, useTableRows } from "../state/hooks";
import { CameraPanel } from "./camera_panel";
import { GatePanel } from "./gate_panel";
import { PlateCard } from "./plate_card";
import { TicketChit } from "./ticket_chit";

export function ExpoScreen() {
  const tickets = useTableRows("ticket");
  const bindings = useTableRows("plate_binding");
  const verdicts = useTableRows("verdict");
  const feed = useFeedStatus();

  return (
    <>
      <GatePanel />

      <Rail title="Orders in" note={`${tickets.length} on the rail`}>
        {tickets.length === 0 && <p className="empty">No tickets yet.</p>}
        {tickets.map((ticket) => (
          <TicketChit key={ticket.ticket_id} ticket={ticket} />
        ))}
      </Rail>

      <section className="pass_band">
        <div className="rail_head">
          <h2 className="display">On the pass</h2>
          <span className="rail_note">{bindings.length} plates</span>
        </div>
        <div className="plate_grid">
          {bindings.length === 0 && <p className="empty">No plates bound to tickets yet.</p>}
          {bindings.map((binding) => (
            <PlateCard
              key={binding.plate_id}
              plate_id={binding.plate_id}
              ticket={tickets.find((ticket) => ticket.ticket_id === binding.ticket_id)}
              ticket_id={binding.ticket_id}
              verdicts={verdicts.filter((verdict) => verdict.plate_id === binding.plate_id)}
              feed={feed}
            />
          ))}
        </div>
      </section>

      <CameraPanel />
    </>
  );
}
