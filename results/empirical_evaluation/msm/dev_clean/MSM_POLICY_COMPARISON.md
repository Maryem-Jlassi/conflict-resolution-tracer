# MSM V1 policy comparison (DEV, clean deterministic)

| Comparison | both_correct | full_lcm only | baseline only | both_wrong | discordant | full_lcm win rate |
|---|---|---|---|---|---|---|
| full_lcm_vs_c_only | 437 | 112 | 38 | 277 | 150 | 0.7467 |
| full_lcm_vs_r_only | 395 | 154 | 94 | 221 | 248 | 0.621 |
| full_lcm_vs_t_only | 524 | 25 | 19 | 296 | 44 | 0.5682 |
| full_lcm_vs_last_write_wins | 469 | 80 | 22 | 293 | 102 | 0.7843 |
| full_lcm_vs_fixed_neutral_trust | 447 | 102 | 34 | 281 | 136 | 0.75 |
| full_lcm_vs_full_minus_recency | 513 | 36 | 14 | 301 | 50 | 0.72 |
| full_lcm_vs_full_minus_confidence | 524 | 25 | 19 | 296 | 44 | 0.5682 |
| full_lcm_vs_full_minus_trust | 447 | 102 | 34 | 281 | 136 | 0.75 |
