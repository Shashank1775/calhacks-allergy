/**
 * MSW handlers for the orders and vision APIs.
 * Orders responses come from the orders team's examples in kitchen/orders/fakes/http/.
 * Used by every test, and in the browser only when WEB_MOCK_API=1 on a dev build.
 */
import { http, HttpResponse } from "msw/http";
import parse_example from "../../../../kitchen/orders/fakes/http/parse.json";
import amend_example from "../../../../kitchen/orders/fakes/http/amend.json";
import send_amendment_example from "../../../../kitchen/orders/fakes/http/send_amendment.json";
import send_example from "../../../../kitchen/orders/fakes/http/send.json";
import send_unanswered_example from "../../../../kitchen/orders/fakes/http/send_unanswered.json";
import type { ParseResult, Ticket } from "../../types/TEMPORARY";

/** The mock keeps one draft, like a single tablet, and the table numbers sent this shift. */
let draft: ParseResult = fresh_draft();
let draft_is_amendment = false;
const sent_numbers = new Set<number>();
/** Vision's frame clock: every health poll sees a newer frame, as a running camera would. */
let frame_t_ms = 0;

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
