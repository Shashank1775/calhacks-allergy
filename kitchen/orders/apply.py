"""Apply a sent draft: write the Ticket, its constraints and exceptions through StateClient, with pass_threshold set from the strictest severity.

Owner: orders (agents)
Step:  order intake (orders)
Reads: sendable ParseResult; OQ-4 threshold defaults (config)
Emits: Ticket via StateClient.put_ticket (tables ticket, ticket_constraint, ticket_exception)
Rules: 1 StateUnavailable leaves the draft unsent; 3 only sendable drafts; 4 allergen units only
Tasks: orders-07, orders-10 (real database, needs partner)

Not built yet (phase gate: PLANNING).
"""
