# Scorecard accuracy numbers

Same content as the SCORECARD ACCURACY block at the end of `test_results.txt`.

```text
Recorded 2026-11-06 by src/record_test_results.py

Data: Lending Club 36-month loans. Train issue years 2013, 2014, test 2015 (time-based split).
Model: logistic regression on WoE features (14 features), score 600 points at 50:1 odds, 20 points to double the odds, higher = safer.

1. Discrimination (higher is better)
model / data                                loans  default_rate    AUC   Gini     KS
----------------------------------------  -------  ------------  -----  -----  -----
Scorecard, train                          120,000        12.96%  0.662  0.325  0.234
Scorecard, test                            60,000        14.83%  0.675  0.350  0.256
Lending Club sub_grade (benchmark), test   60,000        14.83%  0.683  0.365  0.263
Lending Club int_rate (benchmark), test    60,000        14.83%  0.682  0.364  0.263
FICO score alone (benchmark), test         60,000        14.83%  0.602  0.204  0.155

2. Default rate by risk band (A = safest)
train:
band    score_range   loans  default_rate  avg_predicted_pd  lift
----  -------------  ------  ------------  ----------------  ----
A     561 and above  25,182         5.21%             5.27%  0.40
B        550 to 560  23,216         8.89%             8.82%  0.69
C        540 to 549  25,890        12.30%            12.06%  0.95
D        530 to 539  23,149        15.68%            16.04%  1.21
E         below 530  22,563        23.77%            23.67%  1.83
bands rise steadily from A to E: yes

test:
band    score_range   loans  default_rate  avg_predicted_pd  lift
----  -------------  ------  ------------  ----------------  ----
A     561 and above  13,463         5.35%             5.24%  0.36
B        550 to 560  11,431        10.35%             8.81%  0.70
C        540 to 549  12,275        14.00%            12.04%  0.94
D        530 to 539  11,097        18.79%            16.01%  1.27
E         below 530  11,734        27.21%            23.96%  1.83
bands rise steadily from A to E: yes

3. Stability (PSI, metric M56; under 0.10 stable, 0.10-0.25 moderate, over 0.25 large)
score PSI test vs train, 10 deciles: 0.003 (stable)
score PSI test vs train, 5 bands:    0.002
features: 14 stable, 0 moderate, 0 large (largest: inq_last_6mths 0.039)

issue_year   data  default_rate  avg_predicted_pd    AUC   Gini     KS  score_psi
----------  -----  ------------  ----------------  -----  -----  -----  ---------
2013        train        12.16%            12.67%  0.658  0.317  0.230      0.002
2014        train        13.76%            13.25%  0.665  0.330  0.237      0.002
2015         test        14.83%            12.97%  0.675  0.350  0.256      0.003

4. Calibration
test year: predicted default rate 12.97%, actual 14.83% (gap +1.86%). Ranking is stable but the level drifts; an intercept recalibration on recent loans would fix it.

5. Data checks (reports/data_checks.md)
32 passed, 0 failed, 0 skipped
```
