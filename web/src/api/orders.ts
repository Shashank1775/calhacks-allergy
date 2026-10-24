/**
 * Client for the Orders HTTP API (contracts/apis.md#orders-http-api).
 * The screens change orders state only through these calls.
 */
import type { ParseResult, Ticket } from "../types/TEMPORARY";
import { call_api, orders_url } from "./http";

export function parse_note(note: string) {
  return call_api<ParseResult>(orders_url(), "/parse", "POST", { note, source: "typed" });
}

export function answer_question(draft_id: string, question_id: string, value: unknown) {
  return call_api<ParseResult>(orders_url(), `/drafts/${draft_id}/answers`, "POST", { question_id, value });
}

export function send_draft(draft_id: string, number: number) {
  return call_api<Ticket>(orders_url(), `/drafts/${draft_id}/send`, "POST", { number });
}

/** A new draft for revision + 1: the ticket's note plus the change, parsed again. */
export function amend_ticket(ticket_id: string, note: string) {
  return call_api<ParseResult>(orders_url(), `/tickets/${ticket_id}/amend`, "POST", { note });
}

/** Sends an amendment draft. It keeps its ticket's number, so none is sent. */
export function send_amendment(draft_id: string) {
  return call_api<Ticket>(orders_url(), `/drafts/${draft_id}/send`, "POST", {});
}
