/**
 * MSW handlers for the orders, vision and video summary APIs.
 * Orders responses come from the orders team's examples in kitchen/orders/fakes/http/; the summary example follows
 * the service's API in issue #198.
 * Used by every test, and in the browser only when WEB_MOCK_API=1 on a dev build.
 */
import { http, HttpResponse } from "msw/http";
import parse_example from "../../../../kitchen/orders/fakes/http/parse.json";
import amend_example from "../../../../kitchen/orders/fakes/http/amend.json";
import send_amendment_example from "../../../../kitchen/orders/fakes/http/send_amendment.json";
import send_example from "../../../../kitchen/orders/fakes/http/send.json";
import send_unanswered_example from "../../../../kitchen/orders/fakes/http/send_unanswered.json";
import type { ParseResult, Ticket } from "../../types/TEMPORARY";
import type { Summary, SummaryJob } from "../summary";

/** The mock keeps one draft, like a single tablet, and the table numbers sent this shift. */
let draft: ParseResult = fresh_draft();
let draft_is_amendment = false;
const sent_numbers = new Set<number>();
/** Vision's frame clock: every health poll sees a newer frame, as a running camera would. */
let frame_t_ms = 0;
/** How many times each summary job has been polled: the first poll is halfway, the second is done. */
const summary_polls = new Map<string, number>();

function fresh_draft(): ParseResult {
  return structuredClone(parse_example.response) as ParseResult;
}

function all_answered(result: ParseResult): boolean {
  return result.questions.every((question) => question.answer !== undefined);
}

/** Forgets sent table numbers between tests. */
export function reset_orders_mock(): void {
  draft = fresh_draft();
  draft_is_amendment = false;
  sent_numbers.clear();
}

/** Forgets summary jobs between tests. */
export function reset_summary_mock(): void {
  summary_polls.clear();
}

export function orders_handlers(base: string) {
  return [
    http.post(`${base}/parse`, async ({ request }) => {
      const body = (await request.json()) as { note: string };
      draft = { ...fresh_draft(), note: body.note };
      draft_is_amendment = false;
      return HttpResponse.json(draft);
    }),

    http.post(`${base}/tickets/:ticket_id/amend`, () => {
      draft = structuredClone(amend_example.response) as ParseResult;
      draft_is_amendment = true;
      return HttpResponse.json(draft);
    }),

    http.post(`${base}/drafts/:draft_id/answers`, async ({ request }) => {
      const body = (await request.json()) as { question_id: string; value: unknown };
      const questions = draft.questions.map((question) =>
        question.question_id === body.question_id ? { ...question, answer: body.value } : question,
      );
      draft = { ...draft, questions };
      draft.sendable = all_answered(draft);
      return HttpResponse.json(draft);
    }),

    http.post(`${base}/drafts/:draft_id/send`, async ({ request }) => {
      if (!all_answered(draft)) {
        return HttpResponse.json(send_unanswered_example.response, { status: send_unanswered_example.status });
      }
      // An amendment keeps its ticket's number, so its send carries none.
      if (draft_is_amendment) return HttpResponse.json(send_amendment_example.response);
      const body = (await request.json()) as { number: number };
      if (sent_numbers.has(body.number)) {
        return HttpResponse.json(
          { error: { code: "number_in_use", message: `ticket ${body.number} was already sent this shift` } },
          { status: 409 },
        );
      }
      sent_numbers.add(body.number);
      const ticket: Ticket = {
        ...(send_example.response as Ticket),
        ticket_id: `t${body.number}`,
        number: body.number,
        note: draft.note,
      };
      return HttpResponse.json(ticket);
    }),
  ];
}

export function vision_handlers(base: string) {
  return [
    http.post(`${base}/plates/start`, async ({ request }) => {
      const body = (await request.json()) as { ticket_id: string };
      return HttpResponse.json({ plate_id: `plate-${body.ticket_id}` });
    }),

    http.get(`${base}/health`, () => {
      frame_t_ms += 33;
      return HttpResponse.json({ fps: 30, detector: "onnx", last_frame_t_ms: frame_t_ms });
    }),
  ];
}

/** A finished summary of a short recording: a knife carries satay to a plate, and a spoon of unknown history. */
export const SUMMARY_EXAMPLE: Summary = {
  video: { name: "prep.mp4", duration_ms: 90000, frames: 2700, frames_processed: 900 },
  detector: "onnx",
  shift_start_ms: 3000,
  entities: [
    {
      entity_id: "bin-1",
      kind: "bin",
      label: "satay",
      origin: "shift_start",
      first_seen_t_ms: 0,
      last_seen_t_ms: 89900,
    },
    {
      entity_id: "tool-knife-1",
      kind: "tool",
      label: "knife",
      origin: "shift_start",
      first_seen_t_ms: 0,
      last_seen_t_ms: 89900,
    },
    {
      entity_id: "tool-spoon-9",
      kind: "tool",
      label: "spoon",
      origin: "unknown",
      first_seen_t_ms: 60000,
      last_seen_t_ms: 70000,
    },
    {
      entity_id: "plate-1",
      kind: "plate",
      label: "plate",
      origin: "shift_start",
      first_seen_t_ms: 0,
      last_seen_t_ms: 89900,
    },
  ],
  touches: [
    {
      event_id: "e1",
      t_start_ms: 5100,
      t_end_ms: 5600,
      a_id: "tool-knife-1",
      b_id: "bin-1",
      action: "transfer.scoop",
      probability: 0.9,
    },
    {
      event_id: "e2",
      t_start_ms: 9400,
      t_end_ms: 9900,
      a_id: "tool-knife-1",
      b_id: "plate-1",
      action: "transfer.contact",
      probability: 0.95,
    },
    {
      event_id: "e3",
      t_start_ms: 65250,
      t_end_ms: 65700,
      a_id: "tool-spoon-9",
      b_id: "plate-1",
      action: "transfer.contact",
      probability: 0.8,
    },
  ],
  exposure: [
    {
      entity_id: "plate-1",
      kind: "plate",
      unknown_history: true,
      allergens: [{ allergen_unit_id: "peanut", probability: 0.84, chain: [] }],
    },
    {
      entity_id: "tool-knife-1",
      kind: "tool",
      unknown_history: false,
      allergens: [{ allergen_unit_id: "peanut", probability: 0.88, chain: [] }],
    },
    {
      entity_id: "bin-1",
      kind: "bin",
      unknown_history: false,
      allergens: [{ allergen_unit_id: "peanut", probability: 0.98, chain: [] }],
    },
  ],
  text: "At 0:05 the knife scooped from satay (peanut 98%); at 0:09 it touched the plate. At 1:05 a spoon of unknown history touched the plate. The plate could now carry peanut (84%), and anything the spoon carried.",
  warnings: ["Only 900 of 2700 frames were processed."],
};

export function summary_handlers(base: string) {
  return [
    http.post(`${base}/summaries`, () => HttpResponse.json({ summary_id: "s-1", status: "running" }, { status: 202 })),

    http.get(`${base}/summaries/:summary_id`, ({ params }) => {
      const summary_id = String(params.summary_id);
      const polls = (summary_polls.get(summary_id) ?? 0) + 1;
      summary_polls.set(summary_id, polls);
      const job: SummaryJob =
        polls < 2
          ? { summary_id, status: "running", progress: 0.5, summary: null, error: null }
          : { summary_id, status: "done", progress: 1, summary: SUMMARY_EXAMPLE, error: null };
      return HttpResponse.json(job);
    }),
  ];
}
