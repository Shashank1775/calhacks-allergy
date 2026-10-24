# Ticket-note labeling guide

How the ticket-note examples Laya is fine-tuned on were labeled. Generators and verifiers both follow it, so two careful people would produce the same labels. When the guide doesn't cover a case, choose the **safer** label (more constraints, stricter severity, trace-sensitive) and **lower the confidence so a question is asked**.

Each `output` holds the ParseResult fields `constraints`, `exceptions` and `questions` (contracts/schemas/parse_result.schema.json). The question threshold is **0.85**: every field below it must have a question.

## Record format (one JSON object per line)

```json
{"id": "<task prefix>-<category>-<batch>-<n>", "task": "parse_note", "category": "<category>",
 "input": {...}, "output": {...}}
```

- `parse_note` input: `{"source": "typed" | "voice", "note": "<the ticket note>"}`.
- `output` is the labeled answer: `constraints`, `exceptions` and `questions`.

## Allergen units and common mappings

| Unit | Includes (map these to the unit) | Watch out |
|---|---|---|
| `milk` | dairy, cheese, butter, ghee, cream, yogurt, whey, casein, lactose, parmesan, paneer | "lactose intolerant" → milk, `intolerance`. "Dairy-free" → milk |
| `egg` | egg, mayo, aioli, meringue, albumin, custard, hollandaise | Egg pasta → egg + wheat |
| `fish` | fish, anchovy, fish sauce, Worcestershire (anchovy), dashi (bonito), caviar | Not shellfish |
| `shellfish` | shrimp, prawn, crab, lobster, crawfish | Mollusks (clams, oysters, mussels, squid) are NOT this unit: if a note says "shellfish" meaning mollusks, keep `shellfish` and ask whether mollusks matter |
| `tree_nut` | almond, cashew, walnut, pecan, pistachio, hazelnut, macadamia, Brazil nut, pine nut, praline, marzipan, nut butters (not peanut) | Coconut: NOT a tree nut for this system. If the note excludes or allows coconut, use an exception or ignore; never a `tree_nut` constraint from coconut alone |
| `peanut` | peanut, groundnut, peanut oil, satay | "Nuts" alone is ambiguous: tree_nut p 0.7 plus a `new_constraint` question about peanuts |
| `wheat` | wheat, flour, bread, pasta, semolina, durum, spelt, farro, couscous, seitan, panko | "Gluten-free" → wheat (p 0.9) plus a question if barley or rye matter (they're not units; note it in the question) |
| `soy` | soy, soy sauce, tofu, edamame, miso, tempeh, soy lecithin | "SF" is ambiguous (soy-free or shellfish-free): ask |
| `sesame` | sesame, tahini, sesame oil, za'atar, benne, hummus (usually tahini) | |

## parse_note: how to label each field

**allergen_unit_id confidence**
- 0.95 to 0.99: the note names the unit or a clear ingredient of it ("peanut allergy", "no shrimp").
- 0.85 to 0.94: clearly implied ("vegan" implies milk and egg; "lactose intolerant" implies milk).
- 0.50 to 0.84: ambiguous ("nuts", "seafood", "SF", "GF/DF" in odd contexts, or a garbled voice word). Ask.

**severity**
- `anaphylactic`: anaphylaxis, EpiPen, severe, "deathly", life-threatening, "can't even be near it", "throat closes". Confidence 0.95.
- `allergy`: the word allergy or allergic with no qualifier. Confidence 0.88 (no question needed).
- `intolerance`: intolerant, sensitivity, "upsets her stomach", lactose intolerant. Confidence 0.9.
- `preference`: doesn't like, avoids, vegan or vegetarian by choice, "trying to cut out". Confidence 0.85 to 0.9.
- No reason given ("no peanuts", "hold the shrimp"): severity `allergy`, confidence 0.6, and a question with options anaphylactic / allergy / intolerance / preference.

**trace_sensitive**
- `true` with 0.95 when the note says cross-contact, traces, "separate utensils", "can't share fryer", or for `anaphylactic`.
- `false` with 0.95 when the note says traces are OK, "just don't put it in", "a little is fine".
- Not stated, severity `allergy` or stricter: `true`, confidence 0.85 (the strict reading, no question).
- Not stated, severity `intolerance` or `preference` (or severity unknown): `true`, confidence 0.7, and a question ("Do traces matter?" with Yes / No).

**exceptions**: "coconut is fine", "butter is OK but no other dairy", "they can have soy sauce". Record the unit and ingredient; confidence 0.95 when stated outright. A contradictory exception ("dairy-free but cheese is fine") gets confidence 0.6 and a question.

**questions**
- Every field below 0.85 gets exactly one question. `target.kind` is `constraint` (with `index` and `field`), `exception` (with `index`, field `ingredient`), or `new_constraint` (`field` `allergen_unit_id`, no index) when asking whether to add a unit.
- `prompt` is short and plain, for a busy server. `options` has 2 to 4 buttons; `value` is the value the field would take (a unit id, a severity, true or false, or a list of unit ids).
- `question_id`s are `q1`, `q2`, ... in order.

**Also**
- Non-allergen requests (onions, cilantro, spice level, doneness, "extra sauce") → no constraint.
- Notes for several guests ("seat 2 shellfish, seat 4 egg allergy") → one constraint per unit for the ticket (each unit once); use the strictest severity mentioned for that unit.
- Self-corrections ("no dairy — actually just no cheese") → label the final meaning. If it stays unclear, lower confidence and ask.
- Amendment notes ("also no dairy", "add egg allergy") → label only what the amendment adds.
- Voice transcripts: realistic speech-to-text errors ("tree nets", "sesame seats", "shell fish", "pea nut", homophones, missing punctuation, fillers like "um"). If a garbled word is probably an allergen, list it with confidence 0.6 to 0.8 and ask.
- Kosher, halal and keto are not allergen statements by themselves. Vegan → milk and egg (also fish and shellfish) at `preference` unless an allergy is stated.

## Variety (generators)

Vary everything: cuisines, dish names, guest descriptions, sentence length, punctuation, capitalization, server shorthand (w/, no, pls, alrgy, GF, DF, NF, SF, EPI), typos and voice errors. Don't reuse the same note structure twice in a batch. Make about a third of each batch hard cases for its category. Use realistic restaurant language, not textbook sentences.
