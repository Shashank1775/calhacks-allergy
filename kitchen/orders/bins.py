"""Bin labels at shift start: Laya reads each label into an allergen profile, a person can adjust it, then it is saved.

Owner: orders (agents)
Step:  orders
Reads: bin label text and the person's edits (Orders HTTP API /bins); LayaParser.read_bin_label
Emits: AllergenProfile via StateClient.put_bin_profile (bin_profile table)
Rules: fail closed: Laya down means manual entry, never an empty profile; allergen units only

Not built yet.
"""
