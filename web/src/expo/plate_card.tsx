/** One plate on the pass: its stamp, and the reasons when it is held. */
import { latest_verdict, plate_badge } from "../badge";
import { BadgeStamp } from "../components/badge_stamp";
import type { FeedStatus } from "../state/state_source";
import type { Ticket, Verdict } from "../types/TEMPORARY";
import { HoldDetails } from "./hold_details";

type Props = {
  plate_id: string;
  ticket_id: string;
  ticket: Ticket | undefined;
  verdicts: Verdict[];
  feed: FeedStatus;
};

export function PlateCard({ plate_id, ticket_id, ticket, verdicts, feed }: Props) {
  const badge = plate_badge(feed, ticket, verdicts);
  const latest = latest_verdict(verdicts);

  return (
    <article className="plate_card" aria-label={`Plate ${plate_id}`}>
      <div className="chit_row bold">
        <span>{ticket ? `TABLE ${ticket.number}` : `TICKET ${ticket_id}`}</span>
        <span>{plate_id}</span>
      </div>
      <BadgeStamp badge={badge} />

      {badge === "hold" && !ticket && <p>Ticket {ticket_id} isn't on the rail, so its needs are unknown.</p>}
      {badge === "hold" && latest && <HoldDetails verdict={latest} />}
      {badge === "rechecking" && ticket && (
        <p>Ticket changed to revision {ticket.revision}. Waiting for the plate to be checked again.</p>
      )}
      {badge === "waiting" && <p className="muted">Not at the pass yet.</p>}
    </article>
  );
}
