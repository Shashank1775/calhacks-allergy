"""Drafts and tickets: parse a note into a draft with one-tap questions, fill answers, send, and amend.

Owner: orders (agents)
Step:  orders
Reads: note text and source (typed | voice); LayaParser; answers (question_id, value); the current Ticket for amendments
Emits: ParseResult drafts; Ticket via StateClient.put_ticket, with pass_threshold set from the strictest severity
Rules: no unanswered questions: every field below question_threshold gets a question, and only sendable drafts send;
       never drop a mentioned allergen; an amendment writes revision + 1 with the full constraint set;
       StateUnavailable leaves the draft unsent

Not built yet.
"""
