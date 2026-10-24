/**
 * Client for the Orders HTTP API (contracts/apis.md#orders-http-api).
 * The screens change orders state only through these calls.
 */
import type { AllergenProfile, ParseResult, Ticket } from "../types/TEMPORARY";
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

export function read_bin_label(label_text: string) {
  return call_api<AllergenProfile>(orders_url(), "/bins/read", "POST", { label_text });
}

export function save_bin_profile(entity_id: string, profile: AllergenProfile) {
  return call_api<AllergenProfile>(orders_url(), `/bins/${entity_id}`, "PUT", profile);
}
