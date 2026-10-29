-- 02_pl_views.sql
-- Profit and loss views. Definitions follow docs/metric_dictionary.md (metric IDs in comments).
-- Plain SQL; safe to re-run (CREATE OR REPLACE). Run after the data is loaded.
--
-- Layers:
--   fin.v_gl_detail          every ledger line with account, period and cost-centre attributes
--   fin.v_pl_account_month   P&L by account, cost centre and month
--   fin.v_pl_monthly         one row per month with M01-M16
--   fin.v_pl_quarterly       one row per quarter
--   fin.v_pl_annual          one row per fiscal year
--   fin.v_income_statement   tall income statement (line per row) for dashboards
--
-- P&L amounts are positive numbers: revenue = credits - debits, expenses = debits - credits.

-- ---------------------------------------------------------------- line level
CREATE OR REPLACE VIEW fin.v_gl_detail AS
SELECT j.journal_id,
       j.line_no,
       j.posting_date,
       j.period,
       p.fiscal_year,
       p.quarter,
       p.month_name,
       j.account_id,
       a.account_name,
       a.account_type,
       a.statement,
       a.report_group,
       j.cost_center_id,
       cc.cost_center_name,
       j.vendor_id,
       v.vendor_name,
       j.source,
       j.memo,
       j.debit,
       j.credit,
       CASE a.account_type
            WHEN 'Revenue' THEN j.credit - j.debit
            WHEN 'Expense' THEN j.debit - j.credit
       END AS pl_amount                     -- NULL for balance sheet accounts
FROM fin.fact_gl_journal j
JOIN fin.dim_account a  ON a.account_id = j.account_id
JOIN fin.dim_period  p  ON p.period = j.period
LEFT JOIN fin.dim_cost_center cc ON cc.cost_center_id = j.cost_center_id
LEFT JOIN fin.dim_vendor      v  ON v.vendor_id = j.vendor_id;

-- ---------------------------------------------------------------- account level
CREATE OR REPLACE VIEW fin.v_pl_account_month AS
SELECT period,
       fiscal_year,
       quarter,
       account_id,
       account_name,
       account_type,
       report_group,
       cost_center_id,
       cost_center_name,
       SUM(pl_amount) AS amount
FROM fin.v_gl_detail
WHERE statement = 'PL'
GROUP BY period, fiscal_year, quarter, account_id, account_name, account_type,
         report_group, cost_center_id, cost_center_name;

-- ---------------------------------------------------------------- monthly P&L
CREATE OR REPLACE VIEW fin.v_pl_monthly AS
WITH g AS (
    SELECT period, fiscal_year, quarter,
           COALESCE(SUM(pl_amount) FILTER (WHERE report_group = 'Interest Income'),    0) AS interest_income,     -- M01
           COALESCE(SUM(pl_amount) FILTER (WHERE report_group = 'Fee Income'),         0) AS fee_income,          -- M02
           COALESCE(SUM(pl_amount) FILTER (WHERE report_group = 'Interest Expense'),   0) AS interest_expense,    -- M04
           COALESCE(SUM(pl_amount) FILTER (WHERE report_group = 'Credit Costs'),       0) AS provision,           -- M07
           COALESCE(SUM(pl_amount) FILTER (WHERE report_group = 'Operating Expenses'), 0) AS operating_expenses,  -- M08
           COALESCE(SUM(pl_amount) FILTER (WHERE report_group = 'Tax'),                0) AS income_tax           -- M10
    FROM fin.v_gl_detail
    WHERE statement = 'PL'
    GROUP BY period, fiscal_year, quarter
), d AS (
    SELECT g.*,
           interest_income + fee_income      AS total_revenue,         -- M03
           interest_income - interest_expense AS net_interest_income   -- M05
    FROM g
), e AS (
    SELECT d.*, net_interest_income + fee_income AS net_revenue        -- M06
    FROM d
), f AS (
    SELECT e.*, net_revenue - provision - operating_expenses AS pre_tax_profit   -- M09
    FROM e
)
SELECT f.period,
       f.fiscal_year,
       f.quarter,
       f.interest_income,
       f.fee_income,
       f.total_revenue,
       f.interest_expense,
       f.net_interest_income,
       f.net_revenue,
       f.provision,
       f.operating_expenses,
       f.pre_tax_profit,
       f.income_tax,
       f.pre_tax_profit - f.income_tax AS net_income,                                             -- M11
       ROUND((f.pre_tax_profit - f.income_tax) / NULLIF(f.total_revenue, 0), 6) AS net_profit_margin,  -- M12
       ROUND(f.operating_expenses / NULLIF(f.net_revenue, 0), 6)               AS cost_to_income_ratio, -- M13
       dr.avg_loan_book,
       dr.opening_borrowings,
       ROUND(f.interest_income  * 12 / NULLIF(dr.avg_loan_book, 0), 6)         AS yield_on_loans,       -- M14
       ROUND(f.interest_expense * 12 / NULLIF(dr.opening_borrowings, 0), 6)    AS cost_of_funds,        -- M15
       ROUND(f.net_interest_income * 12 / NULLIF(dr.avg_loan_book, 0), 6)      AS net_interest_margin   -- M16
FROM f
LEFT JOIN fin.fact_drivers dr ON dr.period = f.period AND dr.scenario = 'Actual';

-- ---------------------------------------------------------------- quarterly and annual P&L
-- Ratios are recomputed from the summed amounts and annualised: x 12 / months in the period.
CREATE OR REPLACE VIEW fin.v_pl_quarterly AS
SELECT fiscal_year,
       quarter,
       COUNT(*)                    AS months,
       SUM(interest_income)        AS interest_income,
       SUM(fee_income)             AS fee_income,
       SUM(total_revenue)          AS total_revenue,
       SUM(interest_expense)       AS interest_expense,
       SUM(net_interest_income)    AS net_interest_income,
       SUM(net_revenue)            AS net_revenue,
       SUM(provision)              AS provision,
       SUM(operating_expenses)     AS operating_expenses,
       SUM(pre_tax_profit)         AS pre_tax_profit,
       SUM(income_tax)             AS income_tax,
       SUM(net_income)             AS net_income,
       ROUND(SUM(net_income) / NULLIF(SUM(total_revenue), 0), 6)                                   AS net_profit_margin,
       ROUND(SUM(operating_expenses) / NULLIF(SUM(net_revenue), 0), 6)                             AS cost_to_income_ratio,
       ROUND(SUM(interest_income)     * 12.0 / COUNT(*) / NULLIF(AVG(avg_loan_book), 0), 6)        AS yield_on_loans,
       ROUND(SUM(interest_expense)    * 12.0 / COUNT(*) / NULLIF(AVG(opening_borrowings), 0), 6)   AS cost_of_funds,
       ROUND(SUM(net_interest_income) * 12.0 / COUNT(*) / NULLIF(AVG(avg_loan_book), 0), 6)        AS net_interest_margin
FROM fin.v_pl_monthly
GROUP BY fiscal_year, quarter;

CREATE OR REPLACE VIEW fin.v_pl_annual AS
SELECT fiscal_year,
       COUNT(*)                    AS months,
       SUM(interest_income)        AS interest_income,
       SUM(fee_income)             AS fee_income,
       SUM(total_revenue)          AS total_revenue,
       SUM(interest_expense)       AS interest_expense,
       SUM(net_interest_income)    AS net_interest_income,
       SUM(net_revenue)            AS net_revenue,
       SUM(provision)              AS provision,
       SUM(operating_expenses)     AS operating_expenses,
       SUM(pre_tax_profit)         AS pre_tax_profit,
       SUM(income_tax)             AS income_tax,
       SUM(net_income)             AS net_income,
       ROUND(SUM(net_income) / NULLIF(SUM(total_revenue), 0), 6)                                   AS net_profit_margin,
       ROUND(SUM(operating_expenses) / NULLIF(SUM(net_revenue), 0), 6)                             AS cost_to_income_ratio,
       ROUND(SUM(interest_income)     * 12.0 / COUNT(*) / NULLIF(AVG(avg_loan_book), 0), 6)        AS yield_on_loans,
       ROUND(SUM(interest_expense)    * 12.0 / COUNT(*) / NULLIF(AVG(opening_borrowings), 0), 6)   AS cost_of_funds,
       ROUND(SUM(net_interest_income) * 12.0 / COUNT(*) / NULLIF(AVG(avg_loan_book), 0), 6)        AS net_interest_margin
FROM fin.v_pl_monthly
GROUP BY fiscal_year;

-- ---------------------------------------------------------------- tall income statement
-- One row per line per month. line_type: revenue and cost lines are positive amounts, subtotals are results.
CREATE OR REPLACE VIEW fin.v_income_statement AS
SELECT m.period,
       m.fiscal_year,
       m.quarter,
       l.line_order,
       l.line_name,
       l.line_type,
       l.amount
FROM fin.v_pl_monthly m
CROSS JOIN LATERAL (VALUES
    (1,  'Interest Income',            'revenue',  m.interest_income),
    (2,  'Fee Income',                 'revenue',  m.fee_income),
    (3,  'Total Revenue',              'subtotal', m.total_revenue),
    (4,  'Interest Expense',           'cost',     m.interest_expense),
    (5,  'Net Interest Income',        'subtotal', m.net_interest_income),
    (6,  'Net Revenue',                'subtotal', m.net_revenue),
    (7,  'Provision for Credit Losses','cost',     m.provision),
    (8,  'Operating Expenses',         'cost',     m.operating_expenses),
    (9,  'Pre-tax Profit',             'subtotal', m.pre_tax_profit),
    (10, 'Income Tax',                 'cost',     m.income_tax),
    (11, 'Net Income',                 'subtotal', m.net_income)
) AS l (line_order, line_name, line_type, amount);
