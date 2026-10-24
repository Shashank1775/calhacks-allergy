import { describe, expect, it } from "vitest";
import pass_clean from "../../contracts/fixtures/valid/verdict/pass-clean.json";
import hold_peanut from "../../contracts/fixtures/valid/verdict/hold-peanut.json";
import { plate_badge } from "./badge";
import type { FeedStatus } from "./state/state_source";
import type { Ticket, Verdict } from "./types/TEMPORARY";

const pass_verdict = pass_clean as Verdict;
const hold_verdict = hold_peanut as Verdict;

function ticket_for(verdict: Verdict, revision = verdict.ticket_revision): Ticket {
  return {
    ticket_id: verdict.ticket_id,
    number: 1,
    revision,
    created_t_ms: 0,
    source: "typed",
    note: "",
    pass_threshold: verdict.pass_threshold,
    forbidden: [],
  };
}

describe("safety: plate_badge is the only way to PASS", () => {
  it("passes when the feed is live and the latest verdict passes this revision", () => {
    expect(plate_badge("live", ticket_for(pass_verdict), [pass_verdict])).toBe("pass");
  });

  it.each<FeedStatus>(["lost", "connecting"])("never passes while the feed is %s", (feed) => {
    expect(plate_badge(feed, ticket_for(pass_verdict), [pass_verdict])).toBe("feed_lost");
  });

  it("holds when the verdict holds", () => {
    expect(plate_badge("live", ticket_for(hold_verdict), [hold_verdict])).toBe("hold");
  });

  it("rechecks when the ticket was amended after the verdict", () => {
    expect(plate_badge("live", ticket_for(pass_verdict, pass_verdict.ticket_revision + 1), [pass_verdict])).toBe(
      "rechecking",
    );
  });

  it("holds when the plate's ticket is unknown", () => {
    expect(plate_badge("live", undefined, [pass_verdict])).toBe("hold");
  });

  it("does not pass before any verdict exists", () => {
    expect(plate_badge("live", ticket_for(pass_verdict), [])).toBe("waiting");
  });

  it("uses the latest verdict, so a later hold beats an earlier pass", () => {
    const later_hold = {
      ...hold_verdict,
      ticket_id: pass_verdict.ticket_id,
      decided_t_ms: pass_verdict.decided_t_ms + 1,
    };
    expect(plate_badge("live", ticket_for(pass_verdict), [pass_verdict, later_hold])).toBe("hold");
  });

  it("holds when the verdict judged a newer revision than the ticket shows", () => {
    expect(plate_badge("live", ticket_for(pass_verdict, pass_verdict.ticket_revision - 1), [pass_verdict])).toBe(
      "hold",
    );
  });
});
