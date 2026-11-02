-- 04_budget_vs_actual_views.sql
-- Budget vs actual and variance driver views. Definitions follow docs/metric_dictionary.md (M30-M33).
-- Plain SQL; safe to re-run (CREATE OR REPLACE). Run after 02_pl_views.sql.
--
-- Layers:
--   fin.v_bva_account_month        actual, budget and variance by month, account and cost centre
--   fin.v_bva_summary              P&L lines (revenue to net income) by month: actual, budget, variance
--   fin.v_bva_summary_quarterly    the same by quarter
--   fin.v_bva_summary_annual       the same by fiscal year
--   fin.v_bva_cost_center_annual   operating expenses by cost centre and year
--   fin.v_variance_drivers         monthly variance split into volume effect, rate effect and other
--   fin.v_variance_drivers_annual  the same by fiscal year
--
-- Sign convention (M30): a FAVOURABLE variance is POSITIVE.
--   revenue lines: actual - budget; expense, provision and tax lines: budget - actual.
-- Volume and rate effects (M32, M33) use the same sign, so effect + effect + other = variance.
-- Rate = amount / volume, for actual and for budget (an implied rate), so the split is exact:
--   volume effect = (volume actual - volume budget) x rate budget
--   rate effect   = variance - volume effect  (= (rate actual - rate budget) x volume actual)

-- ---------------------------------------------------------------- account level
CREATE OR REPLACE VIEW fin.v_bva_account_month AS
WITH act AS (
    SELECT period, account_id, COALESCE(cost_center_id, '') AS cc, SUM(pl_amount) AS actual
    FROM fin.v_gl_detail
    WHERE statement = 'PL'
    GROUP BY period, account_id, COALESCE(cost_center_id, '')
), bud AS (
    SELECT period, account_id, COALESCE(cost_center_id, '') AS cc, SUM(budget_amount) AS budget
    FROM fin.fact_budget
    GROUP BY period, account_id, COALESCE(cost_center_id, '')
), k AS (
    SELECT COALESCE(a.period, b.period)         AS period,
           COALESCE(a.account_id, b.account_id) AS account_id,
           COALESCE(a.cc, b.cc)                 AS cc,
           COALESCE(a.actual, 0)                AS actual,
           COALESCE(b.budget, 0)                AS budget
    FROM act a
    FULL JOIN bud b ON b.period = a.period AND b.account_id = a.account_id AND b.cc = a.cc
)
SELECT k.period,
       p.fiscal_year,
       p.quarter,
       p.month_end,
       k.account_id,
       ac.account_name,
       ac.account_type,
       ac.report_group,
       NULLIF(k.cc, '')                                   AS cost_center_id,
       cc.cost_center_name,
       k.actual,
       k.budget,
       CASE WHEN ac.account_type = 'Revenue' THEN k.actual - k.budget
            ELSE k.budget - k.actual END                  AS variance,                -- M30
       ROUND((CASE WHEN ac.account_type = 'Revenue' THEN k.actual - k.budget
                   ELSE k.budget - k.actual END) / NULLIF(k.budget, 0), 6) AS variance_pct   -- M31
FROM k
JOIN fin.dim_period  p  ON p.period = k.period
JOIN fin.dim_account ac ON ac.account_id = k.account_id
LEFT JOIN fin.dim_cost_center cc ON cc.cost_center_id = NULLIF(k.cc, '');

-- ---------------------------------------------------------------- P&L lines, monthly
-- is_cost = 1 for cost lines, where variance = budget - actual.
CREATE OR REPLACE VIEW fin.v_bva_summary AS
WITH am AS (
    SELECT period, fiscal_year, quarter, month_end,
           SUM(actual) FILTER (WHERE account_id = 4000) AS ii_a,
           SUM(budget) FILTER (WHERE account_id = 4000) AS ii_b,
           SUM(actual) FILTER (WHERE account_id = 4100) AS fi_a,
           SUM(budget) FILTER (WHERE account_id = 4100) AS fi_b,
           SUM(actual) FILTER (WHERE account_id = 5000) AS ie_a,
           SUM(budget) FILTER (WHERE account_id = 5000) AS ie_b,
           SUM(actual) FILTER (WHERE account_id = 5100) AS pr_a,
           SUM(budget) FILTER (WHERE account_id = 5100) AS pr_b,
           SUM(actual) FILTER (WHERE account_id BETWEEN 6000 AND 6500) AS ox_a,
           SUM(budget) FILTER (WHERE account_id BETWEEN 6000 AND 6500) AS ox_b,
           SUM(actual) FILTER (WHERE account_id = 7000) AS tx_a,
           SUM(budget) FILTER (WHERE account_id = 7000) AS tx_b
    FROM fin.v_bva_account_month
    GROUP BY period, fiscal_year, quarter, month_end
), x AS (
    SELECT am.*,
           COALESCE(ii_a,0) - COALESCE(ie_a,0) + COALESCE(fi_a,0) - COALESCE(pr_a,0) - COALESCE(ox_a,0) AS pbt_a,
           COALESCE(ii_b,0) - COALESCE(ie_b,0) + COALESCE(fi_b,0) - COALESCE(pr_b,0) - COALESCE(ox_b,0) AS pbt_b
    FROM am
), l AS (
    SELECT x.period, x.fiscal_year, x.quarter, x.month_end, v.line_order, v.line_name, v.is_cost, v.actual, v.budget
    FROM x
    CROSS JOIN LATERAL (VALUES
        (1,  'Interest Income',            0, COALESCE(ii_a,0), COALESCE(ii_b,0)),
        (2,  'Fee Income',                 0, COALESCE(fi_a,0), COALESCE(fi_b,0)),
        (3,  'Total Revenue',              0, COALESCE(ii_a,0) + COALESCE(fi_a,0), COALESCE(ii_b,0) + COALESCE(fi_b,0)),
        (4,  'Interest Expense',           1, COALESCE(ie_a,0), COALESCE(ie_b,0)),
        (5,  'Net Interest Income',        0, COALESCE(ii_a,0) - COALESCE(ie_a,0), COALESCE(ii_b,0) - COALESCE(ie_b,0)),
        (6,  'Net Revenue',                0, COALESCE(ii_a,0) - COALESCE(ie_a,0) + COALESCE(fi_a,0),
                                              COALESCE(ii_b,0) - COALESCE(ie_b,0) + COALESCE(fi_b,0)),
        (7,  'Provision for Credit Losses',1, COALESCE(pr_a,0), COALESCE(pr_b,0)),
        (8,  'Operating Expenses',         1, COALESCE(ox_a,0), COALESCE(ox_b,0)),
        (9,  'Pre-tax Profit',             0, pbt_a, pbt_b),
        (10, 'Income Tax',                 1, COALESCE(tx_a,0), COALESCE(tx_b,0)),
        (11, 'Net Income',                 0, pbt_a - COALESCE(tx_a,0), pbt_b - COALESCE(tx_b,0))
    ) AS v (line_order, line_name, is_cost, actual, budget)
)
SELECT period, fiscal_year, quarter, month_end, line_order, line_name, is_cost, actual, budget,
       CASE WHEN is_cost = 1 THEN budget - actual ELSE actual - budget END AS variance,        -- M30
       ROUND((CASE WHEN is_cost = 1 THEN budget - actual ELSE actual - budget END)
             / NULLIF(budget, 0), 6)                                       AS variance_pct     -- M31
FROM l;

-- ---------------------------------------------------------------- P&L lines, quarterly and annual
CREATE OR REPLACE VIEW fin.v_bva_summary_quarterly AS
SELECT fiscal_year, quarter, line_order, line_name, is_cost,
       SUM(actual) AS actual,
       SUM(budget) AS budget,
       CASE WHEN is_cost = 1 THEN SUM(budget) - SUM(actual) ELSE SUM(actual) - SUM(budget) END AS variance,
       ROUND((CASE WHEN is_cost = 1 THEN SUM(budget) - SUM(actual) ELSE SUM(actual) - SUM(budget) END)
             / NULLIF(SUM(budget), 0), 6) AS variance_pct
FROM fin.v_bva_summary
GROUP BY fiscal_year, quarter, line_order, line_name, is_cost;

CREATE OR REPLACE VIEW fin.v_bva_summary_annual AS
SELECT fiscal_year, line_order, line_name, is_cost,
       SUM(actual) AS actual,
       SUM(budget) AS budget,
       CASE WHEN is_cost = 1 THEN SUM(budget) - SUM(actual) ELSE SUM(actual) - SUM(budget) END AS variance,
       ROUND((CASE WHEN is_cost = 1 THEN SUM(budget) - SUM(actual) ELSE SUM(actual) - SUM(budget) END)
             / NULLIF(SUM(budget), 0), 6) AS variance_pct
FROM fin.v_bva_summary
GROUP BY fiscal_year, line_order, line_name, is_cost;

-- ---------------------------------------------------------------- operating expenses by cost centre
CREATE OR REPLACE VIEW fin.v_bva_cost_center_annual AS
SELECT fiscal_year,
       cost_center_id,
       cost_center_name,
       SUM(actual) AS actual,
       SUM(budget) AS budget,
       SUM(budget) - SUM(actual) AS variance,
       ROUND((SUM(budget) - SUM(actual)) / NULLIF(SUM(budget), 0), 6) AS variance_pct
FROM fin.v_bva_account_month
WHERE account_id BETWEEN 6000 AND 6500
GROUP BY fiscal_year, cost_center_id, cost_center_name;

-- ---------------------------------------------------------------- variance drivers (monthly)
-- One row per month and P&L account. Lines with a volume driver are split into volume and rate effects;
-- the other lines (technology, rent, collections, depreciation, tax) have no driver, so their whole
-- variance is shown as other_effect (technology holds the one-off cost of March 2024).
--   Interest Income      volume = avg_loan_book          rate = yield (monthly)
--   Fee Income           volume = originations           rate = fee rate
--   Interest Expense     volume = opening_borrowings     rate = cost of funds (monthly)
--   Provision            volume = opening_loan_book      rate = provision rate on the opening book
--   Salaries             volume = headcount              rate = cost per head
--   Marketing            volume = originations           rate = marketing % of originations
CREATE OR REPLACE VIEW fin.v_variance_drivers AS
WITH acct AS (
    SELECT period, fiscal_year, quarter, month_end, account_id, account_name, account_type,
           SUM(actual) AS actual, SUM(budget) AS budget
    FROM fin.v_bva_account_month
    GROUP BY period, fiscal_year, quarter, month_end, account_id, account_name, account_type
), d AS (
    SELECT a.period, a.fiscal_year, a.quarter, a.month_end, a.account_id, a.account_name, a.account_type,
           a.actual, a.budget,
           CASE WHEN a.account_type = 'Revenue' THEN 1 ELSE -1 END AS sgn,
           CASE a.account_id WHEN 4000 THEN 'Average loan book'
                             WHEN 4100 THEN 'Originations'
                             WHEN 5000 THEN 'Opening borrowings'
                             WHEN 5100 THEN 'Opening loan book'
                             WHEN 6000 THEN 'Headcount'
                             WHEN 6100 THEN 'Originations' END AS volume_driver,
           CASE a.account_id WHEN 4000 THEN 'Yield (monthly)'
                             WHEN 4100 THEN 'Fee rate'
                             WHEN 5000 THEN 'Cost of funds (monthly)'
                             WHEN 5100 THEN 'Provision rate'
                             WHEN 6000 THEN 'Cost per head'
                             WHEN 6100 THEN 'Marketing % of originations' END AS rate_driver,
           CASE a.account_id WHEN 4000 THEN da.avg_loan_book
                             WHEN 4100 THEN da.originations
                             WHEN 5000 THEN da.opening_borrowings
                             WHEN 5100 THEN da.opening_loan_book
                             WHEN 6000 THEN da.headcount
                             WHEN 6100 THEN da.originations END AS volume_actual,
           CASE a.account_id WHEN 4000 THEN db.avg_loan_book
                             WHEN 4100 THEN db.originations
                             WHEN 5000 THEN db.opening_borrowings
                             WHEN 5100 THEN db.opening_loan_book
                             WHEN 6000 THEN db.headcount
                             WHEN 6100 THEN db.originations END AS volume_budget
    FROM acct a
    LEFT JOIN fin.fact_drivers da ON da.period = a.period AND da.scenario = 'Actual'
    LEFT JOIN fin.fact_drivers db ON db.period = a.period AND db.scenario = 'Budget'
), e AS (
    SELECT d.*,
           d.sgn * (d.actual - d.budget) AS variance,
           d.actual / NULLIF(d.volume_actual, 0) AS rate_actual,
           d.budget / NULLIF(d.volume_budget, 0) AS rate_budget
    FROM d
)
SELECT period, fiscal_year, quarter, month_end, account_id, account_name,
       volume_driver, rate_driver,
       volume_actual, volume_budget, rate_actual, rate_budget,
       actual, budget,
       variance,                                                                        -- M30
       CASE WHEN volume_driver IS NOT NULL
            THEN ROUND(sgn * (volume_actual - volume_budget) * rate_budget, 2) END      AS volume_effect,   -- M32
       CASE WHEN volume_driver IS NOT NULL
            THEN ROUND(variance - ROUND(sgn * (volume_actual - volume_budget) * rate_budget, 2), 2) END
                                                                                        AS rate_effect,     -- M33
       CASE WHEN volume_driver IS NULL THEN ROUND(variance, 2) ELSE 0 END               AS other_effect
FROM e;

-- ---------------------------------------------------------------- variance drivers (annual)
CREATE OR REPLACE VIEW fin.v_variance_drivers_annual AS
SELECT fiscal_year, account_id, account_name, volume_driver, rate_driver,
       SUM(actual)                        AS actual,
       SUM(budget)                        AS budget,
       SUM(variance)                      AS variance,
       SUM(volume_effect)                 AS volume_effect,
       SUM(rate_effect)                   AS rate_effect,
       SUM(other_effect)                  AS other_effect
FROM fin.v_variance_drivers
GROUP BY fiscal_year, account_id, account_name, volume_driver, rate_driver;
