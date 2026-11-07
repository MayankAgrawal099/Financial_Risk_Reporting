# Monthly refresh and board report automation

Every month one flow refreshes the data, checks it, builds the board report as a PDF and emails it. Picture: `docs/architecture/power_automate_flow.png`.

| Piece | File | What it does |
|---|---|---|
| Orchestrator | `src/run_monthly_refresh.py` | Reloads the database, runs the 32 data checks, exports the extracts, builds the PDF, writes `reports/latest_report.json` and `reports/refresh_log.csv` |
| Report | `src/build_board_report.py` | Builds `reports/board_report_YYYY-MM.pdf` (5 pages, or 6 with the credit risk page) from the same CSV extracts that feed the Qlik dashboards |
| Email (fallback) | `src/send_report_email.py` | Sends the PDF by SMTP from Python, or writes an `.eml` file with `--dry-run` |
| Flow | Power Automate Desktop flow `Monthly Board Report` | Runs the orchestrator, reads the status file, emails the PDF (or a failure notice) |
| Schedule | Windows Task Scheduler | Starts the flow on the 1st of each month |

## Run it by hand

```
python src/run_monthly_refresh.py --dsn postgresql://postgres:PASSWORD@localhost:5432/fin_risk --lending-club
python src/run_monthly_refresh.py --skip-reload        # keep the data in the database; checks and exports still run (needs FIN_PG_DSN)
python src/run_monthly_refresh.py --skip-db            # no database: rebuild the PDF from the extracts in dashboards/qlik/data
python src/run_monthly_refresh.py --month 2025-06      # a specific month (default: latest in the data)
python src/send_report_email.py --dry-run              # check the email without sending it
```

Set `FIN_PG_DSN` once (the connection string) and the `--dsn` option is not needed.

## What the board report contains

1. Summary: month and year-to-date net income against budget, revenue, total assets, debt-to-equity, and written headlines built from the numbers
2. Profit and loss: every line, month and year to date, actual, budget, variance (favourable positive)
3. Variance drivers: budget to actual net income waterfall, volume and rate effects
4. Balance sheet: month-end position against the prior month, check that assets equal liabilities plus equity, trend charts
5. Credit risk (when the scorecard outputs exist): Gini, AUC, KS, score PSI, default rate by band, actual vs predicted
6. Notes and definitions: sources, sign convention, data check result, metric definitions from `docs/metric_dictionary.md`

## Status file for the flow: `reports/latest_report.json`

| Field | Meaning |
|---|---|
| `status` | `OK`, `WARN` (built, but something was skipped, for example data checks not run) or `FAILED` (nothing to send) |
| `month`, `month_label` | The report month, for example `2025-12` and `December 2025` |
| `pdf_path`, `pdf_name` | The PDF to attach (empty when `FAILED`) |
| `subject`, `body` | Ready-made email subject and plain-text body (a failure notice when `FAILED`) |
| `headlines`, `checks`, `warnings`, `steps` | Detail for the log |
| `error` | Reason when `FAILED` |

A failed data check stops the run before the PDF is built, so numbers that do not tie are never sent.

## Email settings

The Power Automate flow sends the email with its own SMTP action. The Python sender uses environment variables (never stored in the repo):
`SMTP_HOST` (default smtp.gmail.com), `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD` (a Gmail App Password), `MAIL_FROM`, `MAIL_TO`.

## Cloud version (design only, not built)

With a work or school Microsoft account the same flow can run in the cloud: a monthly Recurrence trigger, an HTTP call that reloads the Qlik apps, a call to the same Python job (Azure Function or a desktop flow on a gateway machine), then an Outlook "Send an email (V2)" action with the PDF attached. The Python job and the status file stay the same.
