# Verification Checklist — GitHub Release Preparation

**Date:** 2026-08-21  
**Status:** Complete with caveats

---

## 1. Numeric Claim Traceability

### Method
- Extracted all decimal numbers from `README.md` and `docs/FULL_TECHNICAL_REPORT.md` using regex `\d+\.\d+`.
- Cross-referenced against `docs/_CITABLE_CLAIMS_MASTER_LIST.md`.
- Manual review of flagged items.

### Results

| Check | Status | Notes |
|-------|--------|-------|
| README.md key findings table | **PASS** | All 10 findings trace to master list artifacts |
| README.md architecture section | **PASS** | No numeric claims; diagrams only |
| README.md PHEME comparison | **PASS** | 0.130, 0.160, 0.737, 1.0 all in master list |
| README.md QACC policy table | **PASS** | 8.69%, 42.16%, 20.61% etc. in master list |
| README.md QACC win-share | **PASS** | 53/102, 1.21, [0.424, 0.614] in master list |
| README.md Limitations | **PASS** | All percentages trace to master list |
| Technical report MSM tables | **PASS** | All table cells trace to SWEEP_PLAN_RESULTS.md artifacts |
| Technical report PHEME tables | **PASS** | All values trace to PHEME_FINAL_EVALUATION_REPORT.md |
| Technical report QACC tables | **PASS** | All values trace to QACC_500_MULTIPROVIDER_RESULTS.md |
| Intermediate table values | **CAVEAT** | Some intermediate table cells (e.g., 0.5773, 0.4227) are not individually listed in the master list but derive directly from the cited artifact tables |

### Caveat
The automated grep flagged 62 decimal numbers not verbatim in the master list. Manual review confirms these are:
1. **Section numbers** (e.g., 2.2, 3.1) — not data claims.
2. **Weight values in code examples** (e.g., 1/3, 1/3, 1/3) — schema definitions, not evaluated claims.
3. **Intermediate table cells** (e.g., 0.5773, 0.4227) — these are exact values from the cited artifact tables (`SWEEP_PLAN_RESULTS.md`), but the master list only includes summary statistics, not every cell.

**Verdict:** No unsupported numeric claims were found. The master list covers all headline findings. Intermediate table values are traceable to their source artifacts but are not individually enumerated in the master list. This is acceptable because the master list's purpose is to anchor headline claims, not every table cell.

---

## 2. Mermaid Diagram Validation

### Method
- Reviewed all Mermaid code blocks in `README.md` and `docs/architecture.md`.
- Verified node labels and edge labels match the actual source code.

### Results

| Diagram | Status | Source Verified |
|---------|--------|-----------------|
| Four-stage pipeline | **PASS** | `crt_core/pipeline.py` lines 1–24 |
| Ψ calculation flow | **PASS** | `crt_core/conflict.py` lines 147–199 |
| Concurrency/locking sequence | **PASS** | `crt_core/locking.py` lines 81–108 |
| Evaluation architecture | **PASS** | Track definitions from citable claims |

**Rendering:** All diagrams use standard Mermaid syntax (flowchart, sequenceDiagram) and should render natively on GitHub. No external tooling required.

---

## 3. Plot Reproducibility

### Method
- Ran `docs/regenerate_figures.py` from raw JSON artifacts.
- Verified all 6 PNG + 6 SVG files were generated without errors.

### Results

| Plot | Status | Source Artifact |
|------|--------|-----------------|
| (a) PHEME aware-vs-neutral | **PASS** | `B1_pheme_aware_neutral.json` |
| (b) MSM C/T ratio sweep | **PASS** | `A2_full_sweep_curve.json` |
| (c) MSM theta sensitivity | **PASS** | `A1_theta_sensitivity.json` |
| (d) QACC policy comparison | **PASS** | `QACC_500_MULTIPROVIDER_RESULTS.md` |
| (e) QACC win-share CI | **PASS** | `RUN/analysis.json` |
| (f) MSM component identifiability | **PASS** | `00_SEED_POOLING_REPORT.md` |

**Reproducibility:** All plots are generated from raw JSON by `docs/regenerate_figures.py`. No hand-typed numbers. Re-running the script produces identical figures.

---

## 4. Unfavorable Findings — Presence Check

Verified that all required unfavorable findings are present and NOT softened:

| Finding | Present | Location |
|---------|---------|----------|
| PHEME 13.0% vs LWW 16.0% | **YES** | README Key Findings; Technical Report §3.2 |
| QACC neutral trust/recency limitation | **YES** | README Limitations; Technical Report §3.3 |
| MSM flat C/T sensitivity | **YES** | README Key Findings; Technical Report §3.1 |
| QACC low coverage (20.61%) | **YES** | README Key Findings; Technical Report §3.3 |
| Groq 84.4% rate-limit failure | **YES** | README Limitations; Technical Report §3.3 |
| Pending conflict in concurrent mode | **YES** | README Limitations; Technical Report §4.1 |
| No G-subweight decomposition | **YES** | README Limitations; Technical Report §5 |
| Guardrail 0% firing rate | **YES** | README Key Findings; Technical Report §3.1 |
| C-vs-T contradiction reversal | **YES** | README Key Findings; Technical Report §4.4 |

---

## 5. Final Status

**ALL CHECKS PASSED** with the documented caveat that intermediate table cells are not individually enumerated in the master list but are traceable to cited artifacts. No unsupported claims, softened findings, or missing diagrams were found.

The repository is ready for public GitHub release.
