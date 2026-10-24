"""Orders: ticket notes and bin labels become typed allergen data, through Laya and one-tap questions.

Owner: orders (agents)
Step:  orders
Reads: note text or audio and bin label text from the tablet (Orders HTTP API)
Emits: ParseResult drafts; Ticket via StateClient.put_ticket; AllergenProfile via StateClient.put_bin_profile
Rules: fail closed (never guess when Laya fails); no unanswered questions; allergens, not contacts;
       late changes recheck everything (an amendment is revision + 1)

Not built yet.
"""
