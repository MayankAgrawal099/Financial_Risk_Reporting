-- 00_create_schema.sql
-- Financial Analytics and Risk Reporting System: PostgreSQL schema (PostgreSQL 13 or newer).
-- Plain SQL only, so it runs in psql, pgAdmin or DBeaver.
--
-- WARNING: drops and recreates the schemas fin and risk. Everything loaded in them is replaced.
--
--   fin  = SYNTHETIC company ledgers of the fictional lender Kestrel Credit Ltd (see docs/data_dictionary.md)
--   risk = REAL Lending Club loan sample used for the credit scorecard
--
-- Column names, types and meanings match docs/data_dictionary.md (a test checks this).
-- Load the data with:  python src/load_to_postgres.py

DROP SCHEMA IF EXISTS fin CASCADE;
DROP SCHEMA IF EXISTS risk CASCADE;
CREATE SCHEMA fin;
CREATE SCHEMA risk;

-- ---------------------------------------------------------------- dimensions

CREATE TABLE fin.dim_account (
    account_id      INTEGER      PRIMARY KEY,
    account_name    VARCHAR(60)  NOT NULL,
    account_type    VARCHAR(10)  NOT NULL CHECK (account_type IN ('Asset', 'Liability', 'Equity', 'Revenue', 'Expense')),
    statement       CHAR(2)      NOT NULL CHECK (statement IN ('BS', 'PL')),
    normal_balance  CHAR(1)      NOT NULL CHECK (normal_balance IN ('D', 'C')),
    report_group    VARCHAR(40)  NOT NULL
);

CREATE TABLE fin.dim_cost_center (
    cost_center_id    CHAR(3)      PRIMARY KEY,
    cost_center_name  VARCHAR(40)  NOT NULL
);

CREATE TABLE fin.dim_vendor (
    vendor_id    CHAR(4)       PRIMARY KEY,
    vendor_name  VARCHAR(100)  NOT NULL,
    category     VARCHAR(40)   NOT NULL
);

CREATE TABLE fin.dim_period (
    period       CHAR(7)   PRIMARY KEY,           -- YYYY-MM
    month_start  DATE      NOT NULL,
    month_end    DATE      NOT NULL,
    fiscal_year  SMALLINT  NOT NULL,
    quarter      CHAR(2)   NOT NULL,
    month_name   CHAR(3)   NOT NULL,
    is_opening   BOOLEAN   NOT NULL               -- true only for 2022-12 (opening balances)
);

-- ---------------------------------------------------------------- facts

CREATE TABLE fin.fact_gl_journal (
    journal_id      VARCHAR(16)    NOT NULL,
    line_no         SMALLINT       NOT NULL,
    posting_date    DATE           NOT NULL,
    period          CHAR(7)        NOT NULL REFERENCES fin.dim_period (period),
    account_id      INTEGER        NOT NULL REFERENCES fin.dim_account (account_id),
    debit           NUMERIC(18,2)  NOT NULL DEFAULT 0,
    credit          NUMERIC(18,2)  NOT NULL DEFAULT 0,
    cost_center_id  CHAR(3)        REFERENCES fin.dim_cost_center (cost_center_id),
    vendor_id       CHAR(4)        REFERENCES fin.dim_vendor (vendor_id),
    source          VARCHAR(30)    NOT NULL,
    memo            VARCHAR(80),
    PRIMARY KEY (journal_id, line_no),
    CHECK (debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0))
);
CREATE INDEX ix_gl_period_account ON fin.fact_gl_journal (period, account_id);

CREATE TABLE fin.fact_budget (
    period          CHAR(7)        NOT NULL REFERENCES fin.dim_period (period),
    account_id      INTEGER        NOT NULL REFERENCES fin.dim_account (account_id),
    cost_center_id  CHAR(3)        REFERENCES fin.dim_cost_center (cost_center_id),
    budget_amount   NUMERIC(18,2)  NOT NULL
);
-- cost_center_id is empty for revenue lines, so uniqueness is enforced with COALESCE
CREATE UNIQUE INDEX ux_budget_key ON fin.fact_budget (period, account_id, COALESCE(cost_center_id, ''));

CREATE TABLE fin.fact_drivers (
    period                         CHAR(7)        NOT NULL REFERENCES fin.dim_period (period),
    scenario                       VARCHAR(6)     NOT NULL CHECK (scenario IN ('Actual', 'Budget')),
    originations                   NUMERIC(18,2),
    opening_loan_book              NUMERIC(18,2),
    repayments                     NUMERIC(18,2),
    writeoffs                      NUMERIC(18,2),
    closing_loan_book              NUMERIC(18,2),
    avg_loan_book                  NUMERIC(18,2),
    interest_income                NUMERIC(18,2),
    yield_annual                   NUMERIC(10,6),
    fee_income                     NUMERIC(18,2),
    fee_rate                       NUMERIC(10,6),
    opening_borrowings             NUMERIC(18,2),
    interest_expense               NUMERIC(18,2),
    cost_of_funds_annual           NUMERIC(10,6),
    allowance_closing              NUMERIC(18,2),
    allowance_coverage             NUMERIC(10,6),
    provision                      NUMERIC(18,2),
    chargeoff_rate_monthly         NUMERIC(10,6),
    headcount                      SMALLINT,
    cost_per_head                  NUMERIC(12,2),
    marketing_pct_of_originations  NUMERIC(10,6),
    PRIMARY KEY (period, scenario)
);

-- ---------------------------------------------------------------- Lending Club (REAL data)
-- The column "default" is a reserved word in SQL, so it is always written in double quotes.

CREATE TABLE risk.lending_club_sample (
    id                          BIGINT         PRIMARY KEY,
    loan_status                 VARCHAR(20)    NOT NULL,
    "default"                   SMALLINT       NOT NULL CHECK ("default" IN (0, 1)),
    issue_d                     DATE           NOT NULL,
    issue_year                  SMALLINT       NOT NULL,
    term_months                 SMALLINT,
    emp_length                  VARCHAR(20),
    home_ownership              VARCHAR(20),
    verification_status         VARCHAR(30),
    purpose                     VARCHAR(40),
    addr_state                  CHAR(2),
    initial_list_status         CHAR(1),
    application_type            VARCHAR(20),
    loan_amnt                   NUMERIC(10,2),
    int_rate                    NUMERIC(6,2),
    installment                 NUMERIC(10,2),
    annual_inc                  NUMERIC(14,2),
    dti                         NUMERIC(8,2),
    delinq_2yrs                 SMALLINT,
    fico_range_low              SMALLINT,
    fico_range_high             SMALLINT,
    inq_last_6mths              SMALLINT,
    mths_since_last_delinq      SMALLINT,
    mths_since_last_record      SMALLINT,
    open_acc                    SMALLINT,
    pub_rec                     SMALLINT,
    revol_bal                   NUMERIC(14,2),
    revol_util                  NUMERIC(8,2),
    total_acc                   SMALLINT,
    mort_acc                    SMALLINT,
    pub_rec_bankruptcies        SMALLINT,
    tot_cur_bal                 NUMERIC(14,2),
    total_rev_hi_lim            NUMERIC(14,2),
    bc_util                     NUMERIC(8,2),
    acc_open_past_24mths        SMALLINT,
    collections_12_mths_ex_med  SMALLINT,
    delinq_amnt                 NUMERIC(14,2),
    tax_liens                   SMALLINT,
    chargeoff_within_12_mths    SMALLINT,
    num_tl_90g_dpd_24m          SMALLINT,
    percent_bc_gt_75            NUMERIC(8,2),
    num_actv_bc_tl              SMALLINT,
    grade                       CHAR(1),
    sub_grade                   CHAR(2),
    earliest_cr_line            DATE
);
CREATE INDEX ix_lc_issue_year ON risk.lending_club_sample (issue_year);

COMMENT ON SCHEMA fin  IS 'SYNTHETIC ledgers for the fictional lender Kestrel Credit Ltd';
COMMENT ON SCHEMA risk IS 'REAL Lending Club loan sample (36-month loans issued 2013-2015)';
