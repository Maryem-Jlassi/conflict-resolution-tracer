# MSM V1 policy comparison (official TEST split, clean deterministic)

| Comparison | both_correct | full_lcm only | baseline only | both_wrong | discordant | full_lcm win rate |
|---|---|---|---|---|---|---|
| full_lcm_vs_c_only | 1070 | 283 | 107 | 700 | 390 | 0.7256 |
| full_lcm_vs_r_only | 1006 | 347 | 241 | 566 | 588 | 0.5901 |
| full_lcm_vs_t_only | 1292 | 61 | 75 | 732 | 136 | 0.4485 |
| full_lcm_vs_last_write_wins | 1144 | 209 | 59 | 748 | 268 | 0.7799 |
| full_lcm_vs_fixed_neutral_trust | 1096 | 257 | 87 | 720 | 344 | 0.7471 |
| full_lcm_vs_full_minus_recency | 1264 | 89 | 38 | 769 | 127 | 0.7008 |
| full_lcm_vs_full_minus_confidence | 1292 | 61 | 75 | 732 | 136 | 0.4485 |
| full_lcm_vs_full_minus_trust | 1096 | 257 | 87 | 720 | 344 | 0.7471 |
