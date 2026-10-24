/**
 * MSW handlers for the orders and vision APIs, built from the contract fixtures.
 * Used by every test, and in the browser only when WEB_MOCK_API=1 on a dev build.
 */
import { http, HttpResponse } from "msw/http";
import needs_answer from "../../../../contracts/fixtures/valid/parse_result/needs-answer.json";
import peanut_ticket from "../../../../contracts/fixtures/valid/ticket/peanut-anaphylactic.json";
import satay_profile from "../../../../contracts/fixtures/valid/allergen_profile/bin-satay.json";
import type { ParseResult } from "../../types/TEMPORARY";

/** The mock keeps one draft, like a single tablet. */
let draft: ParseResult = fresh_draft();

function fresh_draft(): ParseResult {
  return structuredClone(needs_answer) as ParseResult;
}

function all_answered(result: ParseResult): boolean {
  return result.questions.every((question) => question.answer !== undefined);
}

export function orders_handlers(base: string) {
  return [
    http.post(`${base}/parse`, async ({ request }) => {
      const body = (await request.json()) as { note: string };
      draft = { ...fresh_draft(), note: body.note };
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
        return HttpResponse.json(
          { error: { code: "unanswered_questions", message: "Answer every question first." } },
          { status: 409 },
        );
      }
      const body = (await request.json()) as { number: number };
      return HttpResponse.json({
        ...peanut_ticket,
        ticket_id: `t${body.number}`,
        number: body.number,
        note: draft.note,
      });
    }),

    http.post(`${base}/bins/read`, () => HttpResponse.json(satay_profile)),

    http.put(`${base}/bins/:entity_id`, async ({ request }) => HttpResponse.json(await request.json())),
  ];
}

export function vision_handlers(base: string) {
  return [
    http.post(`${base}/plates/start`, async ({ request }) => {
      const body = (await request.json()) as { ticket_id: string };
      return HttpResponse.json({ plate_id: `plate-${body.ticket_id}` });
    }),
  ];
}
