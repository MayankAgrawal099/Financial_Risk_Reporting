# Build the "Monthly Board Report" flow in Power Automate (Desktop), step by step

For a first-time user of the Power Automate app on Windows. Picture of the finished flow: `docs/architecture/power_automate_flow.png`.
The flow does five things: run one Python command, read the status file, check whether the run failed, then email the PDF or a failure notice.

## Part 1. Prepare (once)

1. **Make a Gmail App Password** (so the flow can send mail without your real password).
   Google Account > Security > turn on **2-Step Verification** if it is off > search for **App passwords** > create one named `Power Automate` > copy the 16 letters. Keep them out of the repo.
2. **Tell Windows your database connection** so the flow does not carry the password. In PowerShell, run (use your real password):
   ```
   setx FIN_PG_DSN "postgresql://postgres:YOUR_PASSWORD@localhost:5432/fin_risk"
   ```
   Then close Power Automate, VS Code and every terminal, and open them again.
3. **Install the new test dependency:** `pip install -r requirements.txt` (adds `pypdf`).
4. **Test the Python part alone first.** In the repo folder:
   ```
   python src\run_monthly_refresh.py --skip-db
   ```
   It should print `STATUS=WARN` (no data checks without the database) and `REPORT=...board_report_2025-12.pdf`. Open the PDF. Then run it with the database:
   ```
   python src\run_monthly_refresh.py --lending-club
   ```
   Expect `STATUS=OK`. If it says `STATUS=FAILED`, the message after `ERROR=` says why.

## Part 2. Build the flow

1. Open **Power Automate** (the app you showed me). Click **New flow**, name it exactly `Monthly Board Report`, click **Create**. The flow designer opens, with an **Actions** list on the left and an empty workspace.
2. Add the actions in this order. Search the name in the Actions search box, then double-click it or drag it into the workspace.

| # | Action (search for) | Settings |
|---|---|---|
| 1 | **Set variable** | Variable: `ProjectFolder`. Value: `C:\Users\USER\OneDrive\Desktop\Resume Projects\financial-risk-reporting` |
| 2 | **Set variable** | Variable: `MailTo`. Value: your own email address while testing |
| 3 | **Run DOS command** | Command line: `cd /d "%ProjectFolder%" && python src\run_monthly_refresh.py --lending-club`. Leave the output variables as they are. For your first test add `--skip-db` at the end |
| 4 | **Read text from file** | File path: `%ProjectFolder%\reports\latest_report.json`. Store content as: Single text value. Variable produced: `ReportText` |
| 5 | **Convert JSON to custom object** | JSON: `%ReportText%`. Variable produced: `Report` |
| 6 | **If** | First operand `%Report.status%`, operator **Equal to**, second operand `FAILED` |
| 6a | inside the If: **Send email** | SMTP server `smtp.gmail.com`, port `587`, **Enable SSL** on, Send from: your Gmail, Send to: `%MailTo%`, Subject `%Report.subject%`, Body `%Report.body%`, User name: your Gmail, Password: the App Password |
| 7 | **Else** | (the Else block is added with the If) |
| 7a | inside the Else: **Send email** | Same server settings. Subject `%Report.subject%`. Body `%Report.body%`. **Attachments:** `%Report.pdf_path%` |
| 8 | **End** | (closes the If) |

3. Click **Save** (top). Click **Run**. Watch the actions light up as they run. A Python window may flash. When it finishes, check your inbox for the email with the PDF.
4. If your PC has desktop Outlook and you prefer it, you can use **Launch Outlook** and **Send email through Outlook** instead of the SMTP action.

## Part 3. Prove the failure branch

1. In action 3 change the command to add `--month 2031-03` at the end. Run the flow.
2. You should receive an email whose subject starts with `FAILED:` and says `No data for month 2031-03`. No PDF is attached.
3. Remove `--month 2031-03` again.

## Part 4. Schedule it monthly

Power Automate Desktop on its own has no built-in monthly schedule. Use Windows Task Scheduler to start the flow:
1. Start menu > **Task Scheduler** > **Create Basic Task**. Name: `Monthly Board Report`.
2. Trigger: **Monthly**, every month, day **1**, time **08:00**.
3. Action: **Start a program**. Program: `cmd.exe`. Arguments:
   ```
   /c start "" "ms-powerautomate:/console/flow/run?workflowName=Monthly%20Board%20Report&source=Other"
   ```
4. Finish. Right-click the task > **Run** to test it.
The PC must be on and you must be signed in at that time. I could not test this part here. If the task does not start the flow, run the flow by hand for your demo and show the scheduled task as the intended trigger.

## Part 5. Screenshots to keep (save in `docs\Screenshots`)

- `power_automate_flow_designer.png`: the designer with all actions visible
- `power_automate_email_ok.png`: the received email with the PDF attached
- `power_automate_email_failed.png`: the failure email
- `power_automate_task_scheduler.png`: the scheduled task
- Open `reports\refresh_log.csv` and screenshot it too

## If something goes wrong

| What you see | Likely cause | Fix |
|---|---|---|
| Email step fails with an authentication error | Using the normal Gmail password, or 2-Step Verification is off | Use an App Password (Part 1, step 1) |
| `STATUS=FAILED ... data checks failed` | A data check found a mismatch | Open `reports\data_checks.md`; nothing is emailed until it passes |
| `ERROR=database reload failed` or a connection error | PostgreSQL is not running, or `FIN_PG_DSN` is not set for the flow | Start PostgreSQL; redo Part 1 step 2 and reopen Power Automate |
| `python` is not recognised | Python is not on the PATH for the flow | Use the full path, for example `C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe` |
| `Read text from file` cannot find the file | The Python step failed before writing the status file | Run the command in a terminal and read the error |
