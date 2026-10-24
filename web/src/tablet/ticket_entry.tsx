/**
 * Ticket entry: note → Laya's parse → one-tap questions → send.
 *
 * Calls: POST /parse, POST /drafts/{id}/answers, POST /drafts/{id}/send.
 * Rules: no unanswered questions. Send stays disabled until the draft is
 * sendable and every question has an answer; a 409 keeps the questions up.
 * Fail closed: if Laya is down (503 laya_unavailable) we show the error and
 * keep the note, never a guessed result.
 */
import { useState } from "react";
import { allergen_name } from "../allergens";
import { answer_question, parse_note, send_draft } from "../api/orders";
import { Chit } from "../components/chit";
import { ToBeImplemented } from "../components/to_be_implemented";
import type { ParseResult, Question, QuestionOption, Ticket } from "../types/TEMPORARY";

export function TicketEntry() {
  const [note, set_note] = useState("");
  const [table, set_table] = useState("");
  const [draft, set_draft] = useState<ParseResult | null>(null);
  const [sent, set_sent] = useState<Ticket | null>(null);
  const [busy, set_busy] = useState(false);
  const [error, set_error] = useState<string | null>(null);

  const open_questions = draft ? draft.questions.filter((question) => question.answer === undefined) : [];
  const table_number = Number(table);
  const table_ok = Number.isInteger(table_number) && table_number > 0;
  const can_send = draft !== null && draft.sendable && open_questions.length === 0 && table_ok && !busy;

  async function handle_parse() {
    set_busy(true);
    set_error(null);
    set_sent(null);
    const result = await parse_note(note);
    set_busy(false);
    if (result.ok) {
      set_draft(result.data);
    } else if (result.code === "laya_unavailable") {
      // The note stays in the box so the server can fix it or try again.
      set_draft(null);
      set_error("Laya couldn't read this note, so nothing was guessed. Check the note and read it again.");
    } else {
      set_draft(null);
      set_error(`Couldn't read the note: ${result.message}`);
    }
  }

  async function handle_answer(question: Question, option: QuestionOption) {
    if (!draft) return;
    set_busy(true);
    set_error(null);
    const result = await answer_question(draft.draft_id, question.question_id, option.value);
    set_busy(false);
    if (result.ok) {
      set_draft(result.data);
    } else {
      set_error(`Couldn't save that answer: ${result.message}`);
    }
  }

  async function handle_send() {
    if (!draft || !can_send) return;
    set_busy(true);
    set_error(null);
    const result = await send_draft(draft.draft_id, table_number);
    set_busy(false);
    if (result.ok) {
      set_sent(result.data);
      set_draft(null);
      set_note("");
      set_table("");
    } else if (result.code === "unanswered_questions") {
      set_error("Orders says a question is still open. Answer it, then send again.");
    } else if (result.code === "number_in_use") {
      set_error(`Table ${table_number} already has a ticket this shift. Check the table number.`);
    } else {
      set_error(`Couldn't send: ${result.message}`);
    }
  }

  return (
    <Chit label="New ticket" wide>
      <div className="chit_row bold">
        <span>NEW TICKET</span>
        <span>SRC: TYPED</span>
      </div>
      <hr className="dashed" />

      <label className="field">
        <span>Guest's note</span>
        <textarea value={note} onChange={(event) => set_note(event.target.value)} rows={3} />
      </label>
      <button type="button" className="btn" onClick={handle_parse} disabled={busy || note.trim() === ""}>
        READ NOTE
      </button>
      <ToBeImplemented>Speaking the note (POST /transcribe). The iPad needs HTTPS for its microphone.</ToBeImplemented>

      {draft && (
        <>
          <hr className="dashed" />
          <DraftSummary draft={draft} />
          {draft.questions.map((question) => (
            <QuestionBlock key={question.question_id} question={question} busy={busy} on_answer={handle_answer} />
          ))}
          <hr className="dashed" />
          <label className="field">
            <span>Table</span>
            <input type="number" min={1} value={table} onChange={(event) => set_table(event.target.value)} />
          </label>
          <button type="button" className="btn" onClick={handle_send} disabled={!can_send}>
            SEND TICKET
          </button>
          {open_questions.length > 0 && (
            <p className="muted">
              {open_questions.length} open question{open_questions.length === 1 ? "" : "s"}. Send unlocks when every one
              is answered.
            </p>
          )}
        </>
      )}

      {sent && (
        <p className="bold">
          SENT · TABLE {sent.number} · REV {sent.revision} · FORBIDS {sent.forbidden.map(allergen_name).join(", ")}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Chit>
  );
}

export function DraftSummary({ draft }: { draft: ParseResult }) {
  return (
    <div>
      <p className="bold">LAYA READ:</p>
      <ul>
        {draft.constraints.map((constraint, index) => (
          <li key={index}>
            NO {allergen_name(constraint.allergen_unit_id).toUpperCase()} · {constraint.severity}
            {constraint.trace_sensitive ? " · traces matter" : ""}
          </li>
        ))}
        {draft.exceptions.map((exception, index) => (
          <li key={`x${index}`}>
            FINE: {exception.ingredient} ({allergen_name(exception.allergen_unit_id)})
          </li>
        ))}
      </ul>
    </div>
  );
}

export function QuestionBlock({
  question,
  busy,
  on_answer,
}: {
  question: Question;
  busy: boolean;
  on_answer: (question: Question, option: QuestionOption) => void;
}) {
  const answered = question.answer !== undefined;
  return (
    <fieldset className="question">
      <legend>
        {answered ? "✓ " : "? "}
        {question.prompt}
      </legend>
      <div className="option_row">
        {question.options.map((option) => {
          const chosen = answered && Object.is(question.answer, option.value);
          return (
            <button
              key={option.label}
              type="button"
              className={chosen ? "btn" : "btn btn_ghost"}
              aria-pressed={chosen}
              disabled={busy}
              onClick={() => on_answer(question, option)}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
