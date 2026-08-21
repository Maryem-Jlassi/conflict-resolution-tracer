# MSM V1 DEV vs TEST comparison (clean deterministic)

Population: DEV = 48 personas (864 units), TEST = 120 personas (2160 units).
Identical frozen deriver, trust, policy, thresholds, and metrics on both splits.
| split | policy | coverage | strict | selective | overwrite | resolved/total |
|---|---|---|---|---|---|---|
| dev | full_lcm | 0.5694 | 0.6354 | 0.7033 | 60 | 492/864 |
| dev | c_only | 0.8241 | 0.5498 | 0.573 | 175 | 712/864 |
| dev | r_only | 0.0556 | 0.566 | 0.5208 | 0 | 48/864 |
| dev | t_only | 0.5938 | 0.6285 | 0.653 | 72 | 513/864 |
| dev | last_write_wins | 0.9641 | 0.5683 | 0.5894 | 187 | 833/864 |
| dev | fixed_neutral_trust | 0.3241 | 0.5567 | 0.6321 | 43 | 280/864 |
| dev | full_minus_recency | 0.7014 | 0.61 | 0.6865 | 86 | 606/864 |
| dev | full_minus_confidence | 0.5197 | 0.6285 | 0.6748 | 66 | 449/864 |
| dev | full_minus_trust | 0.5417 | 0.5567 | 0.5876 | 97 | 468/864 |
| test | full_lcm | 0.5463 | 0.6264 | 0.6864 | 158 | 1180/2160 |
| test | c_only | 0.8199 | 0.5449 | 0.5827 | 439 | 1771/2160 |
| test | r_only | 0.0556 | 0.5773 | 0.3333 | 0 | 120/2160 |
| test | t_only | 0.5917 | 0.6329 | 0.6518 | 184 | 1278/2160 |
| test | last_write_wins | 0.962 | 0.5569 | 0.5789 | 511 | 2078/2160 |
| test | fixed_neutral_trust | 0.2931 | 0.5477 | 0.613 | 108 | 633/2160 |
| test | full_minus_recency | 0.7032 | 0.6028 | 0.6669 | 236 | 1519/2160 |
| test | full_minus_confidence | 0.5176 | 0.6329 | 0.6494 | 170 | 1118/2160 |
| test | full_minus_trust | 0.5292 | 0.5477 | 0.5783 | 260 | 1143/2160 |

Component identifiability (claim-bearing units):
| split | component | identifiability |
|---|---|---|
| dev | R | 491/833 |
| dev | C | 475/833 |
| dev | T | 543/833 |
| test | R | 1203/2078 |
| test | C | 1177/2078 |
| test | T | 1367/2078 |
