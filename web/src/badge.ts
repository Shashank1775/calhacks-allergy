/**
 * The one function that decides which badge a plate shows.
 *
 * Rules: fail closed, plain-code verdict, late changes recheck everything.
 * PASS only when the feed is live and the plate's latest verdict says pass for
 * the ticket's latest revision. It never computes a verdict, and no other code
 * may render PASS.
 */
import type { FeedStatus } from "./state/state_source";
import type { Ticket, Verdict } from "./types/TEMPORARY";

export type Badge = "pass" | "hold" | "rechecking" | "feed_lost" | "waiting";

export const BADGE_TEXT: Record<Badge, string> = {
  pass: "PASS",
  hold: "HOLD",
  rechecking: "RECHECKING",
  feed_lost: "FEED LOST",
  waiting: "NO VERDICT YET",
};

export function plate_badge(feed: FeedStatus, ticket: Ticket | undefined, verdicts_for_plate: Verdict[]): Badge {
  if (feed !== "live") return "feed_lost";

  // A plate bound to a ticket we can't see has unknown requirements.
  if (!ticket) return "hold";

  const latest = latest_verdict(verdicts_for_plate);
  if (!latest) return "waiting";

  // The ticket changed after this verdict; it no longer answers for the plate.
  if (latest.ticket_revision < ticket.revision) return "rechecking";
  if (latest.ticket_revision !== ticket.revision) return "hold";

  return latest.decision === "pass" ? "pass" : "hold";
}

/** The most recently decided verdict; on a tie, the later row wins. */
export function latest_verdict(verdicts: Verdict[]): Verdict | undefined {
  let latest: Verdict | undefined;
  for (const verdict of verdicts) {
    if (!latest || verdict.decided_t_ms >= latest.decided_t_ms) latest = verdict;
  }
  return latest;
}
