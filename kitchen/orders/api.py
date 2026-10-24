"""Orders HTTP API: /v1/transcribe, /v1/parse, /answers, /send, /amend, /profiles/bins, /confirm, /menu/import, /v1/health.

Owner: orders (agents)
Step:  order intake (orders)
Reads: HTTP requests from the outputs tablet and chef screens
Emits: ParseResult, Ticket, AllergenProfile as JSON; standard error body; CORS for ORDERS_CORS_ORIGINS
Rules: 3 /send returns 409 unanswered_questions; 1 jev_unavailable is an error, not a result
Tasks: orders-01 (skeleton), orders-06, orders-07, orders-08, orders-09, orders-11

Not built yet (phase gate: PLANNING).
"""
