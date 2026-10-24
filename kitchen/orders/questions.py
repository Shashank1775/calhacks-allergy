"""One-tap questions: every field below question_threshold becomes a question; answers fill their target; sendable only when all are answered.

Owner: orders (agents)
Step:  order intake (orders)
Reads: ParseResult draft, answers (question_id, value)
Emits: ParseResult with questions, answers and the sendable flag
Rules: 3 no send with an open question
Tasks: orders-01 (question builder), orders-06 (answers and send gate)

Not built yet.
"""
