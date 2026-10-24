"""Late changes: open a draft at revision + 1 pre-filled with the previous constraints, then write the full new revision on send.

Owner: orders (agents)
Step:  order intake (orders)
Reads: current Ticket (StateClient.get_ticket), amendment note
Emits: ParseResult draft; Ticket at revision + 1 via StateClient.put_ticket
Rules: 9 new revision with the full constraint set; old rows untouched; no silent constraint drop
Tasks: orders-08

Not built yet.
"""
