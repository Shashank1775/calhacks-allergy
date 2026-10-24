"""Orders: ticket notes become typed allergen data through Laya and one-tap questions; demo bins are hard-coded.

Owner: orders (agents)
Step:  orders
Reads: note text or audio from the tablet (Orders HTTP API); DEMO_BINS (bins.py)
Emits: ParseResult drafts; Ticket via StateClient.put_ticket; AllergenProfile via StateClient.put_bin_profile
Rules: fail closed (never guess when Laya fails); no unanswered questions; allergens, not contacts;
       late changes recheck everything (an amendment is revision + 1)
"""
