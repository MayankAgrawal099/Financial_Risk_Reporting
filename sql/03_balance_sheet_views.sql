-- 03_balance_sheet_views.sql
-- Balance sheet views. Definitions follow docs/metric_dictionary.md (M20-M28). Run after 02_pl_views.sql.
-- Plain SQL; safe to re-run (CREATE OR REPLACE).
--
-- Layers:
--   fin.v_balance_sheet_account_month   month-end balance of every balance sheet account
--   fin.v_balance_sheet_monthly         one row per month-end, wide (assets, liabilities, equity, ratios)
--   fin.v_balance_sheet_year_end        the December rows plus annual return on equity
--   fin.v_balance_sheet_statement       tall layout (one line per row) for dashboards
--
-- Sign convention in the account view: assets are debit - credit, liabilities and equity are
-- credit - debit. The allowance (a contra-asset) is therefore NEGATIVE there and subtracts from assets.
-- The wide view shows the allowance as a positive number, as defined in M21.
-- The ledger has no closing entries, so equity = share capital + opening retained earnings
-- + cumulative net income (M26). The opening position is period 2022-12.

-- ---------------------------------------------------------------- account level
CREATE OR REPLACE VIEW fin.v_balance_sheet_account_month AS
WITH bs AS (
    SELECT account_id, account_name, account_type, report_group
    FROM fin.dim_account
    WHERE statement = 'BS'
), grid AS (
    SELECT p.period, p.fiscal_year, p.quarter, p.month_end, p.is_opening,
           b.account_id, b.account_name, b.account_type, b.report_group
    FROM fin.dim_period p
    CROSS JOIN bs b
), mv AS (
    SELECT period, account_id, SUM(debit) AS debit, SUM(credit) AS credit
    FROM fin.fact_gl_journal
    GROUP BY period, account_id
)
SELECT g.period,
       g.fiscal_year,
       g.quarter,
       g.month_end,
       g.is_opening,
       g.account_id,
       g.account_name,
       g.account_type,
       g.report_group,
       COALESCE(m.debit, 0)  AS debit_movement,
       COALESCE(m.credit, 0) AS credit_movement,
       SUM(CASE WHEN g.account_type = 'Asset'
                THEN COALESCE(m.debit, 0) - COALESCE(m.credit, 0)
                ELSE COALESCE(m.credit, 0) - COALESCE(m.debit, 0) END)
           OVER (PARTITION BY g.account_id ORDER BY g.period) AS balance
FROM grid g
LEFT JOIN mv m ON m.period = g.period AND m.account_id = g.account_id;

-- ---------------------------------------------------------------- monthly balance sheet (wide)
CREATE OR REPLACE VIEW fin.v_balance_sheet_monthly AS
WITH b AS (
    SELECT period, fiscal_year, quarter, month_end, is_opening,
           COALESCE(SUM(balance) FILTER (WHERE account_id = 1000), 0)  AS cash,
           COALESCE(SUM(balance) FILTER (WHERE account_id = 1100), 0)  AS gross_loans,         -- M20
           COALESCE(-SUM(balance) FILTER (WHERE account_id = 1110), 0) AS allowance,           -- M21 (positive)
           COALESCE(SUM(balance) FILTER (WHERE account_id = 1200), 0)  AS fixed_assets,
           COALESCE(SUM(balance) FILTER (WHERE account_id = 1300), 0)  AS other_assets,
           COALESCE(SUM(balance) FILTER (WHERE account_type = 'Asset'), 0)     AS total_assets,       -- M24
           COALESCE(SUM(balance) FILTER (WHERE account_id = 2000), 0)  AS borrowings,
           COALESCE(SUM(balance) FILTER (WHERE account_id = 2100), 0)  AS accrued_expenses,
           COALESCE(SUM(balance) FILTER (WHERE account_id = 2200), 0)  AS taxes_payable,
           COALESCE(SUM(balance) FILTER (WHERE account_type = 'Liability'), 0) AS total_liabilities,  -- M25
           COALESCE(SUM(balance) FILTER (WHERE account_id = 3000), 0)  AS share_capital,
           COALESCE(SUM(balance) FILTER (WHERE account_id = 3100), 0)  AS retained_earnings_opening
    FROM fin.v_balance_sheet_account_month
    GROUP BY period, fiscal_year, quarter, month_end, is_opening
), c AS (
    SELECT b.*,
           pl.net_income AS month_net_income,
           SUM(COALESCE(pl.net_income, 0)) OVER (ORDER BY b.period) AS cumulative_net_income
    FROM b
    LEFT JOIN fin.v_pl_monthly pl ON pl.period = b.period
)
SELECT c.period,
       c.fiscal_year,
       c.quarter,
       c.month_end,
       c.is_opening,
       (RIGHT(c.period, 2) IN ('03', '06', '09', '12')) AS is_quarter_end,
       (RIGHT(c.period, 2) = '12')                      AS is_year_end,
       c.cash,
       c.gross_loans,
       c.allowance,
       c.gross_loans - c.allowance                      AS net_loans,                  -- M22
       c.fixed_assets,
       c.other_assets,
       c.total_assets,
       c.borrowings,
       c.accrued_expenses,
       c.taxes_payable,
       c.total_liabilities,
       c.share_capital,
       c.retained_earnings_opening,
       c.cumulative_net_income,
       c.share_capital + c.retained_earnings_opening + c.cumulative_net_income AS total_equity,    -- M26
       c.total_liabilities + c.share_capital + c.retained_earnings_opening + c.cumulative_net_income
                                                        AS total_liabilities_and_equity,
       c.total_assets - (c.total_liabilities + c.share_capital + c.retained_earnings_opening
                         + c.cumulative_net_income)     AS balance_check,              -- must be 0
       ROUND(c.allowance / NULLIF(c.gross_loans, 0), 6) AS allowance_coverage,         -- M23
       ROUND(c.borrowings / NULLIF(c.share_capital + c.retained_earnings_opening + c.cumulative_net_income, 0), 4)
                                                        AS debt_to_equity,             -- M27
       c.month_net_income,
       ROUND(c.month_net_income * 12 / NULLIF(c.share_capital + c.retained_earnings_opening
                                              + c.cumulative_net_income, 0), 6) AS return_on_equity   -- M28, one month annualised
FROM c;

-- ---------------------------------------------------------------- year-end snapshot
-- return_on_equity here is the annual figure: net income / average of the 12 month-end equity values.
CREATE OR REPLACE VIEW fin.v_balance_sheet_year_end AS
SELECT m.period,
       m.fiscal_year,
       m.month_end,
       m.cash, m.gross_loans, m.allowance, m.net_loans, m.fixed_assets, m.other_assets, m.total_assets,
       m.borrowings, m.accrued_expenses, m.taxes_payable, m.total_liabilities,
       m.share_capital, m.retained_earnings_opening, m.cumulative_net_income, m.total_equity,
       m.balance_check,
       m.allowance_coverage,
       m.debt_to_equity,
       a.return_on_equity
FROM fin.v_balance_sheet_monthly m
LEFT JOIN (
    SELECT fiscal_year,
           ROUND(SUM(month_net_income) * 12.0 / COUNT(*) / NULLIF(AVG(total_equity), 0), 6) AS return_on_equity
    FROM fin.v_balance_sheet_monthly
    WHERE NOT is_opening
    GROUP BY fiscal_year
) a ON a.fiscal_year = m.fiscal_year
WHERE m.is_year_end;

-- ---------------------------------------------------------------- tall statement
-- line_type: asset, liability, equity or subtotal. The allowance is shown negative inside assets.
CREATE OR REPLACE VIEW fin.v_balance_sheet_statement AS
SELECT m.period,
       m.fiscal_year,
       m.quarter,
       m.month_end,
       m.is_quarter_end,
       m.is_year_end,
       l.line_order,
       l.line_name,
       l.line_type,
       l.amount
FROM fin.v_balance_sheet_monthly m
CROSS JOIN LATERAL (VALUES
    (1,  'Cash and Bank',                     'asset',     m.cash),
    (2,  'Loans Receivable (Gross)',          'asset',     m.gross_loans),
    (3,  'Allowance for Credit Losses',       'asset',     -m.allowance),
    (4,  'Net Loans',                         'subtotal',  m.net_loans),
    (5,  'Fixed Assets (Net)',                'asset',     m.fixed_assets),
    (6,  'Prepaid and Other Assets',          'asset',     m.other_assets),
    (7,  'Total Assets',                      'subtotal',  m.total_assets),
    (8,  'Borrowings',                        'liability', m.borrowings),
    (9,  'Accrued Expenses',                  'liability', m.accrued_expenses),
    (10, 'Taxes Payable',                     'liability', m.taxes_payable),
    (11, 'Total Liabilities',                 'subtotal',  m.total_liabilities),
    (12, 'Share Capital',                     'equity',    m.share_capital),
    (13, 'Retained Earnings (Opening)',       'equity',    m.retained_earnings_opening),
    (14, 'Net Income Since Opening',          'equity',    m.cumulative_net_income),
    (15, 'Total Equity',                      'subtotal',  m.total_equity),
    (16, 'Total Liabilities and Equity',      'subtotal',  m.total_liabilities_and_equity)
) AS l (line_order, line_name, line_type, amount);
