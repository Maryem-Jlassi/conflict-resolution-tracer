# PHEME Stance Extraction Specification (Phase 3)

## Model Identity
- **Version:** pheme_stance_v1.0_deterministic
- **Type:** Deterministic rule-based (no LLM)
- **Frozen:** Yes — lexicons and thresholds are fixed and will not be modified.

## Inputs (gold-blind)
Per-tweet metadata only:
- `text`
- `entities.urls`
- `entities.media`
- `user.followers_count`
- `retweet_count`
- `favorite_count`

## Excluded Inputs (gold boundary)
- `annotation.json.true`
- `annotation.json.misinformation`
- `annotation.json.category`
- `annotation.json.links[].position`
- `annotation.json.links[].mediatype`
- Any future tweet or reaction
- Any post-decision information

## Transformation
1. **Lexicon score** = (#support_tokens) / (#support_tokens + #deny_tokens)
   - Support lexicon: {0} tokens
   - Deny lexicon: {1} tokens
2. **Evidence score** = same as C evidence_score (0.25·log_followers + 0.30·url + 0.15·media + 0.15·text_len + 0.15·log_engagement)
3. **Combined** = 0.6 · lexicon + 0.4 · evidence
4. **Stance:**
   - combined >= 0.6 → `support` (confidence = combined)
   - combined <= 0.4 → `deny` (confidence = 1 - combined)
   - else → `comment` (confidence = 0.3)

## Information Boundary Proof
- `current gold → stance extraction` = **FALSE**
- `current gold → C` = **FALSE**
- `current gold → R` = **FALSE**
- `current gold → T` = **FALSE**
- `current gold → provenance features` = **FALSE**

## Output
`PHEME_STANCE_EXTRACTION_RESULTS.jsonl` — one row per tweet, executed ONCE, cached permanently.
