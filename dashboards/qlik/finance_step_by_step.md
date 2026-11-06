# Finance sheet: step-by-step build in Qlik Sense (website / Qlik Cloud)

For someone using Qlik Sense for the first time. Target picture: `finance_sheet_mockup.png` (numbered 1 to 7; the numbers below match).
Qlik changes its menus now and then, so a button may be named a little differently. Look for the nearest option with the same meaning, and send me a screenshot if you are stuck.

Before you start: the app `DA04 Finance` must be loaded (guide section 1) and the master dimensions and measures created (guide section 2, `finance_measures.md`).

## Words you need

- **Sheet**: one page of the dashboard. **Edit sheet** (top right) switches editing on; **Done editing** switches it off.
- **Assets panel** (left, while editing): tabs **Charts** (objects you can drag), **Fields** (raw fields) and **Master items** (your saved dimensions and measures).
- **Properties panel** (right, appears when you click an object): tabs **Data**, **Sorting**, **Add-ons**, **Appearance**.
- **Dimension**: what you group by (Month). **Measure**: a calculation (Net Income).
- Drag an object from the Charts tab onto the sheet, drag its edges to resize, drag its middle to move.

## Colours to use everywhere

| Meaning | Colour | Hex |
|---|---|---|
| Favourable / good | Green | `#2da44e` |
| Unfavourable / bad | Red | `#d1242f` |
| Actual | Blue | `#1f6feb` |
| Budget | Amber | `#bf8700` |
| Neutral / other | Grey | `#9aa5b1` |

In Qlik, type a hex colour by clicking the colour swatch and pasting the code into the box at the bottom of the picker.
For colours written as an expression, use `RGB(45,164,78)` for green and `RGB(209,36,47)` for red.

## Step 0. Create the sheet

1. Open the app. Click **Sheets** (top), then **Create new sheet**.
2. Click the sheet title area, name it `Finance - P&L and Budget vs Actual`, add the description `Kestrel Credit Ltd, 2023-2025`.
3. Click **Edit sheet** if it is not already in edit mode.

Plan the grid first: filters across the top, five KPIs in one row, then two charts per row (as in the picture), and the wide chart last.

## 1. Filter pane (Year, Quarter, Month)

1. Assets panel > Charts > drag **Filter pane** to the top of the sheet. Stretch it across the full width.
2. Click **Add dimension** > choose `Year`. Click **Add dimension** again for `Quarter`, then `Month`.
3. Properties > Appearance > General > turn **Show titles** off (the field names are enough).
4. Click **Done editing**, then click `2025` in the Year box. It turns green. This is your starting selection.
5. Save it: click **Bookmarks** (bookmark icon in the top bar) > **Create new bookmark** > name `FY2025`. You can return to this view with one click.
6. Click **Edit sheet** again to continue.

## 2. Five KPI boxes

Build the first one, then copy it (right-click the object > **Duplicate**) and change only the measure.

1. Charts > drag **KPI** onto the sheet. Resize to about one fifth of the width.
2. Properties > Data > **Add measure** > Master items > pick `Total Revenue`.
3. Appearance > General > **Show titles** on, title `Total Revenue`.
4. Appearance > Presentation: set the layout to show the value large (**Show measure label** off, the title is enough).
5. Repeat four times for: `Net Income`, `Net Income Variance %`, `Net Profit Margin`, `Cost-to-Income Ratio` (set each title to the same name).
6. Colour the variance KPI: select it > Appearance > **Colors and legend** > switch to custom > **By expression** (or "Conditional colour"), paste:
   `If(Sum({<[P&L Line]={'Net Income'}>} Variance) >= 0, RGB(45,164,78), RGB(209,36,47))`
   and tick **Expression is a color code**. With Year = 2025 it turns red. If your KPI has no colour-by-expression option, leave it black; it is not essential.
7. Number formats come from the master measures, so you do not need to set them again.

## 3. The P&L table (picture number 3)

1. Charts > drag **Table** onto the left side of the second row. (If you see both Table and Pivot table, use **Table**.)
2. Properties > Data > Columns > **Add dimension** > `P&L Line`.
3. **Add measure** four times: `Actual`, `Budget`, `Variance`, `Variance %` (all master measures).
4. **Sort the rows in P&L order.** Properties > Sorting: switch off **Auto** for `P&L Line`, then choose **Custom** > **Sort by expression** and paste `Min([Line Order])`, ascending. Without this, the lines appear alphabetically.
5. Totals: Appearance > Presentation > **Totals** off (adding up different lines makes no sense).
6. Colour the variance numbers: click the `Variance` column (Data > Columns > Variance) > **Text color** > **By expression**, paste
   `If(Sum(Variance) >= 0, RGB(45,164,78), RGB(209,36,47))`, tick **Expression is a color code**. Do the same for `Variance %`.
7. Title: Appearance > General > `P&L: actual vs budget`.

Check: with Year = 2025 the Net Income row reads 1,962,349 | 3,580,855 | -1,618,506 | -45.2%.

## 4. Net income by month (combo chart, picture number 4)

1. Charts > drag **Combo chart** to the right side of the second row.
2. Data > Dimension: `Month`. Measures: add `Net Income`, then `Net Income Budget`.
3. For each measure, click it and choose how to draw it: `Net Income` as **Bar**, `Net Income Budget` as **Line** (turn on markers if offered).
4. Colours: Appearance > Colors and legend > custom > **By measure**. Set the bar colour to blue `#1f6feb` and the line to amber `#bf8700`. If your menu only offers colour schemes, pick one with two clearly different colours; exact shades do not matter.
5. Sorting: Month ascending (it already sorts by date).
6. Appearance > Legend: show, position top.
7. Title: `Net income by month: actual vs budget`.

What to see in 2025: the bars fall far below the amber line from April to August. That is when the provision overspend hits.

## 5. Waterfall (budget to actual net income, picture number 5)

1. Charts > drag **Waterfall chart** to the left side of the third row.
2. Data > Measures > **Add measure** > this time choose **Expression** (not a master item) and paste the first row from the Waterfall table in `finance_measures.md`. Set **Label** to the item name.
   Add the eight items in this order: `Net Income Budget`, `Interest Income`, `Fee Income`, `Interest Expense`, `Provision for Credit Losses`, `Operating Expenses`, `Income Tax`, then `Net Income (actual, subtotal)`.
3. Click the last item and switch on **Show as subtotal** (it becomes the final total bar).
4. Appearance > Colors and legend: **Positive** green `#2da44e`, **Negative** red `#d1242f`, **Subtotal** blue `#1f6feb`.
5. Title: `Budget to actual net income`.

The first bar (budget) may show in the positive colour. That is fine.

If the waterfall refuses the measures: use a **Bar chart** with dimension `P&L Line`, measure `Variance`, and select only the six lines (see section 5 of `finance_sheet_guide.md`).

Check: the bars read -324,082 | +21,588 | +23,333 | -1,636,914 | -241,935 | +539,502, ending at 1,962,349.

## 6. Operating expense variance by cost centre (picture number 6)

1. Charts > drag **Bar chart** to the right side of the third row.
2. Data > Dimension `Cost Center`; measure `Opex Variance by Cost Centre` (master measure).
3. Appearance > Presentation > **Orientation**: horizontal. Turn on **Value labels**.
4. Sorting: switch off Auto, sort by **Measure** ascending (most negative at the bottom as in the picture).
5. Colours: Appearance > Colors and legend > custom > **By expression**, paste
   `If(Sum({<[Account ID]={">=6000<=6500"}>} [Account Variance]) >= 0, RGB(45,164,78), RGB(209,36,47))`
   and tick **Expression is a color code**.
6. Title: `Operating expense variance by cost centre`. Subtitle: `Green = under budget, red = over budget`.

Check for 2025: Risk and Collections -139,336, Sales and Marketing -116,890, Operations +9,886, Corporate +3,100, Technology +1,305.

## 7. What drove the variance (stacked bars, picture number 7)

1. Charts > drag another **Bar chart** to the bottom, full width.
2. Data > Dimension `Driver Account`; measures `Volume Effect`, `Rate Effect`, `Other Effect`.
3. Appearance > Presentation: **Stacked**, orientation horizontal.
4. Colours: custom > **By measure**: Volume blue `#1f6feb`, Rate amber `#bf8700`, Other grey `#9aa5b1`.
5. Sorting: by measure, ascending. Legend on, at the bottom.
6. Title: `What drove the variance: volume, rate and other effects`. Subtitle: `Volume = volume vs budget at the budget rate. Rate = the rest.`

Check for 2025: Provision for Credit Losses is almost all a rate effect (about -1,661,256; volume +24,342).

## 8. Footer note

Charts > **Text & image** > drag to the bottom, type: `Source: synthetic ledger, Kestrel Credit Ltd, 2023-2025. Variance: favourable is positive. Definitions in docs/metric_dictionary.md.` Make the font small and grey.

## 9. Finish and screenshot

1. Click **Done editing**. Select Year = 2025 only (or click the bookmark `FY2025`).
2. Compare with the numbers in guide section 4. Also click `2023` and check Net Income 1,487,978.
3. Take a screenshot of the whole sheet (browser zoom 67% or 80% fits it on one screen; or use your browser's full-page screenshot).
4. Save as `docs\Screenshots\qlik_finance_sheet_2025.png`, and one more for 2023 if you like.

## If something goes wrong

| What you see | Likely cause | Fix |
|---|---|---|
| "Incomplete visualization" | No dimension or measure added yet | Open Data tab and add them |
| A measure shows `-` | Expression pasted with curly quotes or a missing bracket | Re-copy from `finance_measures.md` |
| Months in the wrong order | Month sorted as text | Sorting > Month > numerically ascending |
| P&L lines in alphabetical order | Step 3.4 not done | Sort by expression `Min([Line Order])` |
| All values are the three-year total | No year selected | Click 2025 in the filter pane |
| A `$Syn` table in the data model | A field name is shared by two tables | Send me a screenshot of the data model |
