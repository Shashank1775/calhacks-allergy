/**
 * Amending a sent ticket ("also no dairy"): pick the ticket, type the change, answer the
 * questions, send. Orders re-reads the whole note and the plate is checked again.
 *
 * Reads: ticket (state source). Calls: POST /tickets/{id}/amend, POST /drafts/{id}/answers,
 * POST /drafts/{id}/send.
 * Rules: no unanswered questions (Send stays disabled until the draft is sendable and every
 * question has an answer); late changes recheck everything (the verdict service rechecks on
 * send; expo shows RECHECKING meanwhile).
 */
import { useState } from "react";
import { allergen_name } from "../allergens";
import { amend_ticket, answer_question, send_amendment } from "../api/orders";
import { Chit } from "../components/chit";
import { useTableRows } from "../state/hooks";
import type { ParseResult, Question, QuestionOption, Ticket } from "../types/TEMPORARY";
import { DraftSummary, QuestionBlock } from "./ticket_entry";

export function AmendTicket() {
  const tickets = useTableRows("ticket");
  const [ticket_id, set_ticket_id] = useState("");
  const [change, set_change] = useState("");
  const [draft, set_draft] = useState<ParseResult | null>(null);
  const [sent, set_sent] = useState<Ticket | null>(null);
  const [busy, set_busy] = useState(false);
  const [error, set_error] = useState<string | null>(null);

  const ticket = tickets.find((row) => row.ticket_id === ticket_id);
  const open_questions = draft ? draft.questions.filter((question) => question.answer === undefined) : [];
  const can_send = draft !== null && draft.sendable && open_questions.length === 0 && !busy;

  function pick(id: string) {
    set_ticket_id(id);
    set_draft(null);
    set_sent(null);
    set_error(null);
  }

  async function handle_read() {
    if (!ticket) return;
    set_busy(true);
    set_error(null);
    set_sent(null);
    const result = await amend_ticket(ticket.ticket_id, change);
    set_busy(false);
    if (result.ok) {
      set_draft(result.data);
    } else if (result.code === "laya_unavailable") {
      set_draft(null);
      set_error("Laya couldn't read this change, so nothing was guessed. Check it and read it again.");
    } else {
      set_draft(null);
      set_error(`Couldn't read the change: ${result.message}`);
    }
  }

  async function handle_answer(question: Question, option: QuestionOption) {
    if (!draft) return;
    set_busy(true);
    set_error(null);
    const result = await answer_question(draft.draft_id, question.question_id, option.value);
    set_busy(false);
    if (result.ok) set_draft(result.data);
    else set_error(`Couldn't save that answer: ${result.message}`);
  }

  async function handle_send() {
    if (!draft || !can_send) return;
    set_busy(true);
    set_error(null);
    const result = await send_amendment(draft.draft_id);
    set_busy(false);
    if (result.ok) {
      set_sent(result.data);
      set_draft(null);
      set_change("");
    } else if (result.code === "unanswered_questions") {
      set_error("Orders says a question is still open. Answer it, then send again.");
    } else if (result.code === "ticket_changed") {
      set_draft(null);
      set_error("Another change to this ticket landed first. Read your change again.");
    } else {
      set_error(`Couldn't send: ${result.message}`);
    }
  }

  return (
    <Chit label="Amend a ticket" wide>
      <div className="chit_row bold">
        <span>AMEND A TICKET</span>
        <span>{ticket ? `TABLE ${ticket.number} · REV ${ticket.revision}` : "—"}</span>
      </div>
      <hr className="dashed" />

      {tickets.length === 0 && <p className="muted">No sent tickets yet.</p>}
      {tickets.length > 0 && (
        <label className="field">
          <span>Ticket</span>
          <select value={ticket_id} onChange={(event) => pick(event.target.value)}>
            <option value="">Pick a ticket</option>
            {tickets.map((row) => (
              <option key={row.ticket_id} value={row.ticket_id}>
                Table {row.number} · forbids {row.forbidden.map(allergen_name).join(", ") || "nothing"}
              </option>
            ))}
          </select>
        </label>
      )}

      {ticket && (
        <>
          <p className="note">“{ticket.note}”</p>
          <label className="field">
            <span>What changed?</span>
            <textarea value={change} onChange={(event) => set_change(event.target.value)} rows={2} />
          </label>
          <button type="button" className="btn" onClick={handle_read} disabled={busy || change.trim() === ""}>
            READ CHANGE
          </button>
        </>
      )}

      {draft && (
        <>
          <hr className="dashed" />
          <DraftSummary draft={draft} />
          {draft.questions.map((question) => (
            <QuestionBlock key={question.question_id} question={question} busy={busy} on_answer={handle_answer} />
          ))}
          <button type="button" className="btn" onClick={handle_send} disabled={!can_send}>
            SEND AMENDMENT
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
          AMENDED · TABLE {sent.number} · REV {sent.revision} · FORBIDS {sent.forbidden.map(allergen_name).join(", ")}
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
