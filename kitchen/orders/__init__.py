"""Order intake: turn a typed or spoken ticket note into exact, typed allergen constraints.

Owner: orders (agents)
Step:  order intake (orders)
Reads: note text or audio from the tablet (Orders HTTP API)
Emits: ParseResult (draft), Ticket via StateClient.put_ticket
Rules: 1 never guess on failure; 3 no send with open questions; 4 allergens not contacts; 9 amend = revision + 1
Tasks: orders-01 .. orders-11

Not built yet.
"""
