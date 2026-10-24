"""Orders HTTP API: /transcribe, /parse, /drafts/{id}/answers, /drafts/{id}/send, /tickets/{id}/amend, /bins/read, /bins/{id}, /health.

Owner: orders (agents)
Step:  orders
Reads: HTTP requests from the outputs tablet screen
Emits: ParseResult, Ticket, AllergenProfile as JSON; the standard error body; CORS for ORDERS_CORS_ORIGINS
Rules: no unanswered questions: /send returns 409 unanswered_questions; fail closed: laya_unavailable is an error, not a result

Not built yet.
"""
