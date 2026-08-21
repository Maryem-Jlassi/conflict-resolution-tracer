# MSM V1 final TEST results (clean deterministic)

- N_TEST_units = 2160
- N_TEST_units_with_claims = 2078
- N_TEST_units_without_claims = 82

| Policy | Coverage | Strict Accuracy | Selective Accuracy | Overwrite | Decisions |
|---|---|---|---|---|---|
| full_lcm | 0.5463 | 0.6264 | 0.6864 | 158 | 2160 |
| c_only | 0.8199 | 0.5449 | 0.5827 | 439 | 2160 |
| r_only | 0.0556 | 0.5773 | 0.3333 | 0 | 2160 |
| t_only | 0.5917 | 0.6329 | 0.6518 | 184 | 2160 |
| last_write_wins | 0.962 | 0.5569 | 0.5789 | 511 | 2160 |
| fixed_neutral_trust | 0.2931 | 0.5477 | 0.613 | 108 | 2160 |
| full_minus_recency | 0.7032 | 0.6028 | 0.6669 | 236 | 2160 |
| full_minus_confidence | 0.5176 | 0.6329 | 0.6494 | 170 | 2160 |
| full_minus_trust | 0.5292 | 0.5477 | 0.5783 | 260 | 2160 |
