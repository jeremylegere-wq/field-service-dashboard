# Build a Maintenance Planning Dashboard in Power BI

A hands-on setup guide with sample data, Power Query (M) code and DAX, for building a scheduled-maintenance planning model: backlog, overdue work, capacity versus demand, a seasonal long-term forecast, and management adjustments.

Everything in the kit is synthetic. There are no real customers, sites, people or volumes. You can follow the guide with the supplied CSV files, then swap in your own data using the mapping in section 11.

**Time needed:** about 2 to 3 hours for the model and the first two pages.
**You need:** Power BI Desktop (free, Windows). Excel or a text editor is useful for looking at the CSVs.

---

## 0. What you will have at the end

| Page | What it answers | Key visuals |
|---|---|---|
| 1. Operations | What is overdue, where, and how old? | KPI cards, bubble map, overdue heatmap, gauges |
| 2. Capacity and Forecast | Do we have enough engineer hours for the next six months? | Capacity vs demand columns, rolling averages, utilisation line, gap chart, seasonal index |
| 3. Booking Letters | How well do customer offers convert to confirmed bookings? | Funnel, trend lines, adherence by planner |

The model underneath is a small star schema, a calendar table, a few disconnected "what-if" parameter tables, and a governed table of management adjustments.

---

## 1. What is in the kit

```
sample-data/
  jobs.csv                    3,757 maintenance jobs (open and closed), Jan 2025 to May 2027
  work_orders.csv             1,731 linked work orders (source of truth for status)
  sites.csv                   33 fictional sites with coordinates
  planned_visits.csv          260 visits from a planning tool, next 120 days
  booking_letters.csv         600 customer offer-to-confirmation journeys
  management_adjustments.csv  5 example hours adjustments with reason and approver
scripts/
  generate_sample_data.py     regenerates the CSVs (seeded, so identical every time)
docs/
  SETUP-GUIDE.md              this file
```

The data is built around an "as of" date of **16 November 2026**. You will store that date in one place (a Settings table), so the sample data keeps working no matter what today's date is.

### Data dictionary

**jobs.csv** (one row per maintenance job)

| Column | Example | Notes |
|---|---|---|
| JobID | MJ-100001 | Unique key |
| SiteID | S032 | Links to sites.csv |
| Region | Island | East, South, North, Central, West, Island |
| Modality | X-Ray | CT, MR, X-Ray, Ultrasound |
| DueDate | 2025-01-22 | Date the maintenance is due |
| EstimatedDurationMin | 90 | Minutes of work |
| LifecycleStatus | Open | Open, Assigned, Assigned ANC, Closed. **Assigned ANC means assigned to an engineer but not yet confirmed with the customer.** |
| ScheduledDateTime | 2026-12-03 08:00 | **Text**, with `-` when nothing is booked (deliberately messy, like a real export) |
| ClosedDate | 2025-02-14 | Blank while open |
| AssignedTo | Gus | Engineer first name |
| AssignmentLeader | WO-50001 | Linked work order, blank if none |
| DebriefPending | N | Y if the visit is done but paperwork is outstanding |

**work_orders.csv**: WorkOrderNumber, WorkOrderStatus. The status here is more current than the status on the job, which is why you will correct the job status from it in Power Query.

**sites.csv**: SiteID, SiteName, Region, City, Latitude, Longitude.

**planned_visits.csv**: PlanID, SiteID, Region, Modality, PlannedDate, DurationMin, TravelMin, CustomerContacted (Y/N), Locked (Y/N).

**booking_letters.csv**: JourneyID, Planner, Region, Modality, OfferDate, FollowUp1Date, FollowUp2Date, ConfirmDate, Outcome.

**management_adjustments.csv**: Month, Region, Modality, Hours, Reason, ApprovedBy. This is the "judgement" input: hours added to or taken from forecast demand for known events.

---

## 2. Step 1: Put the files in a folder and create a parameter

1. Copy the `sample-data` folder somewhere stable, for example `C:\PowerBI\maintenance-sample\`.
2. Open Power BI Desktop and start a blank report.
3. **Home, Transform data** opens Power Query.
4. **Home, Manage Parameters, New Parameter**:
   - Name: `DataFolder`
   - Type: Text
   - Current Value: `C:\PowerBI\maintenance-sample\sample-data\`

Using a parameter means moving the folder later is a one-line change, and anyone you share the file with can point it at their own copy.

If you prefer the Advanced Editor, create a blank query named `DataFolder` and paste:

```m
"C:\PowerBI\maintenance-sample\sample-data\" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]
```

---

## 3. Step 2: Power Query queries (Advanced Editor)

For each block below: **Home, New Source, Blank Query**, then **Home, Advanced Editor**, delete the default text, paste the code, click **Done**, and rename the query to the name in the heading.

> **Order matters a little.** Create `fnLoadCsv` first, then `Sites` and `WorkOrders`, then `Jobs` (it merges `WorkOrders`).

### 3.1 `fnLoadCsv` (a reusable function)

Right-click the finished query and confirm it shows the function icon. It reads any CSV in the folder and promotes the header row.

```m
(fileName as text) as table =>
let
    Folder = if Text.EndsWith(DataFolder, "\") then DataFolder else DataFolder & "\",
    Source = Csv.Document(
        File.Contents(Folder & fileName),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true])
in
    Promoted
```

### 3.2 `Sites`

```m
let
    Source = fnLoadCsv("sites.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"SiteID", type text}, {"SiteName", type text}, {"Region", type text},
         {"City", type text}, {"Latitude", type number}, {"Longitude", type number}},
        "en-AU"
    )
in
    Typed
```

Then set the **Data category** later in the model view: Latitude as *Latitude*, Longitude as *Longitude*, City as *City*. That is what lets a map plot the points.

### 3.3 `WorkOrders`

```m
let
    Source = fnLoadCsv("work_orders.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"WorkOrderNumber", type text}, {"WorkOrderStatus", type text}}
    ),
    Distinct = Table.Distinct(Typed, {"WorkOrderNumber"})
in
    Distinct
```

`Table.Distinct` guarantees one row per work order, so the merge in the next query cannot duplicate jobs.

### 3.4 `Jobs`

This is the main fact table and the most instructive query. It does four things: types the columns, parses the messy scheduled-date text, corrects stale status from the work order, and adds year-month keys.

```m
let
    Source = fnLoadCsv("jobs.csv"),

    // 1. Types (culture en-AU so dates and decimals parse predictably)
    Typed = Table.TransformColumnTypes(
        Source,
        {{"JobID", type text}, {"SiteID", type text}, {"Region", type text}, {"Modality", type text},
         {"DueDate", type date}, {"EstimatedDurationMin", Int64.Type}, {"LifecycleStatus", type text},
         {"ScheduledDateTime", type text}, {"ClosedDate", type date}, {"AssignedTo", type text},
         {"AssignmentLeader", type text}, {"DebriefPending", type text}},
        "en-AU"
    ),

    // 2. Clean the scheduled-date text: "-" and blank mean "not scheduled"
    Trimmed = Table.TransformColumns(
        Typed,
        {{"ScheduledDateTime", each Text.Trim(_ ?? ""), type text}}
    ),
    SchedClean = Table.AddColumn(
        Trimmed, "ScheduledDate_Clean_DT",
        each if [ScheduledDateTime] = "-" or [ScheduledDateTime] = "" then null
             else try DateTime.FromText([ScheduledDateTime], "en-AU") otherwise null,
        type nullable datetime
    ),

    // 3. Correct status from the linked work order (the work order is the source of truth)
    Merged = Table.NestedJoin(
        SchedClean, {"AssignmentLeader"}, WorkOrders, {"WorkOrderNumber"}, "WO", JoinKind.LeftOuter
    ),
    Expanded = Table.ExpandTableColumn(Merged, "WO", {"WorkOrderStatus"}, {"WorkOrderStatus"}),
    Corrected = Table.AddColumn(
        Expanded, "LifecycleStatus_Corrected",
        each if [WorkOrderStatus] <> null then [WorkOrderStatus] else [LifecycleStatus],
        type text
    ),

    // 4. Helper columns
    Hours = Table.AddColumn(Corrected, "EstimatedHours", each [EstimatedDurationMin] / 60, type number),
    DueYM = Table.AddColumn(Hours, "DueYM", each Date.Year([DueDate]) * 12 + Date.Month([DueDate]), Int64.Type),
    ClosedYM = Table.AddColumn(
        DueYM, "ClosedYM",
        each if [ClosedDate] = null then null else Date.Year([ClosedDate]) * 12 + Date.Month([ClosedDate]),
        Int64.Type
    ),
    SchedYM = Table.AddColumn(
        ClosedYM, "ScheduledYM",
        each if [ScheduledDate_Clean_DT] = null then null
             else Date.Year([ScheduledDate_Clean_DT]) * 12 + Date.Month([ScheduledDate_Clean_DT]),
        Int64.Type
    ),
    // 5. Drop the helper column and return
    Final = Table.RemoveColumns(SchedYM, {"WorkOrderStatus"})
in
    Final
```

Why each step exists:

| Step | Why |
|---|---|
| `Text.Trim(_ ?? "")` | The `??` operator turns null into an empty string so `Text.Trim` cannot fail on a missing value |
| `try ... otherwise null` | One malformed date must not break the refresh; it becomes null instead |
| `JoinKind.LeftOuter` | Keep every job, even if it has no work order |
| `LifecycleStatus_Corrected` | Reports must use this column, not the raw status |
| `DueYM`, `ClosedYM`, `ScheduledYM` | A single integer (year x 12 + month) that sorts, subtracts and compares easily |

### 3.5 `PlannedVisits`

```m
let
    Source = fnLoadCsv("planned_visits.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"PlanID", type text}, {"SiteID", type text}, {"Region", type text}, {"Modality", type text},
         {"PlannedDate", type date}, {"DurationMin", Int64.Type}, {"TravelMin", Int64.Type},
         {"CustomerContacted", type text}, {"Locked", type text}},
        "en-AU"
    ),
    Hours = Table.AddColumn(
        Typed, "PlannedHours", each ([DurationMin] + [TravelMin]) / 60, type number
    ),
    Flags = Table.AddColumn(Hours, "Contacted", each [CustomerContacted] = "Y", type logical)
in
    Flags
```

### 3.6 `BookingLetters`

```m
let
    Source = fnLoadCsv("booking_letters.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"JourneyID", type text}, {"Planner", type text}, {"Region", type text}, {"Modality", type text},
         {"OfferDate", type date}, {"FollowUp1Date", type date}, {"FollowUp2Date", type date},
         {"ConfirmDate", type date}, {"Outcome", type text}},
        "en-AU"
    ),
    DaysToConfirm = Table.AddColumn(
        Typed, "DaysToConfirm",
        each if [ConfirmDate] = null then null else Duration.Days([ConfirmDate] - [OfferDate]),
        Int64.Type
    )
in
    DaysToConfirm
```

### 3.7 `ManagementAdjustments`

```m
let
    Source = fnLoadCsv("management_adjustments.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"Month", type date}, {"Region", type text}, {"Modality", type text},
         {"Hours", type number}, {"Reason", type text}, {"ApprovedBy", type text}},
        "en-AU"
    )
in
    Typed
```

### 3.8 `Regions` and `Modalities` (small dimension tables)

These let one slicer filter every fact table. Create two more blank queries.

`Regions`:

```m
let
    Source = Sites,
    Region = Table.SelectColumns(Source, {"Region"}),
    Distinct = Table.Distinct(Region)
in
    Distinct
```

`Modalities`:

```m
let
    Source = #table(type table [Modality = text], {{"CT"}, {"MR"}, {"X-Ray"}, {"Ultrasound"}})
in
    Source
```

### 3.9 `Calendar`

```m
let
    StartDate = #date(2025, 1, 1),
    EndDate   = #date(2027, 12, 31),
    Dates = List.Dates(StartDate, Duration.Days(EndDate - StartDate) + 1, #duration(1, 0, 0, 0)),
    AsTable = Table.FromList(Dates, Splitter.SplitByNothing(), {"Date"}, null, ExtraValues.Error),
    Typed = Table.TransformColumnTypes(AsTable, {{"Date", type date}}),
    Year = Table.AddColumn(Typed, "Year", each Date.Year([Date]), Int64.Type),
    MonthNo = Table.AddColumn(Year, "MonthNo", each Date.Month([Date]), Int64.Type),
    MonthName = Table.AddColumn(MonthNo, "Month", each Date.ToText([Date], "MMM", "en-AU"), type text),
    YM = Table.AddColumn(MonthName, "YearMonthNumber", each Date.Year([Date]) * 12 + Date.Month([Date]), Int64.Type),
    YearMonth = Table.AddColumn(YM, "YearMonth", each Date.ToText([Date], "yyyy-MM"), type text),
    MonthStart = Table.AddColumn(YearMonth, "MonthStart", each Date.StartOfMonth([Date]), type date),
    Workday = Table.AddColumn(MonthStart, "IsWorkday", each Date.DayOfWeek([Date], Day.Monday) < 5, type logical)
in
    Workday
```

Public holidays are not included. To model them, add a `Holidays` list and change `IsWorkday` to `Date.DayOfWeek(...) < 5 and not List.Contains(Holidays, [Date])`.

### 3.10 Close and apply

Click **Close and Apply**. Power BI loads all tables. If a query shows an error, see the troubleshooting table in section 10.

---

## 4. Step 3: Build the model

### 4.1 Mark the date table

Select `Calendar`, **Table tools, Mark as date table**, choose `Date`. This is required for the time-intelligence functions used in the rolling averages.

### 4.2 Sort columns

- `Calendar[Month]`: Sort by column, `MonthNo`.
- `Calendar[YearMonth]`: Sort by column, `MonthStart`.

### 4.3 Relationships (Model view)

Delete any relationships Power BI auto-detected that are not in this table, then create:

| From (one side) | To (many side) | Active |
|---|---|---|
| Calendar[Date] | Jobs[DueDate] | Yes |
| Calendar[Date] | Jobs[ClosedDate] | **No** (inactive, used with `USERELATIONSHIP`) |
| Calendar[Date] | PlannedVisits[PlannedDate] | Yes |
| Calendar[Date] | BookingLetters[OfferDate] | Yes |
| Regions[Region] | Jobs[Region] | Yes |
| Regions[Region] | PlannedVisits[Region] | Yes |
| Regions[Region] | BookingLetters[Region] | Yes |
| Regions[Region] | ManagementAdjustments[Region] | Yes |
| Modalities[Modality] | Jobs[Modality] | Yes |
| Modalities[Modality] | PlannedVisits[Modality] | Yes |
| Modalities[Modality] | BookingLetters[Modality] | Yes |
| Modalities[Modality] | ManagementAdjustments[Modality] | Yes |
| Sites[SiteID] | Jobs[SiteID] | Yes |
| Sites[SiteID] | PlannedVisits[SiteID] | Yes |

Do **not** relate `Regions` to `Sites`. Both `Regions` and `Sites` can reach `Jobs`, and a second path creates an ambiguous model. Use `Regions[Region]` for slicers and `Sites` for map coordinates.

Cross-filter direction: single, from the one side to the many side, everywhere.

Hide `Region` and `Modality` on the fact tables (right-click, Hide in report view) so report builders always use the dimension versions.

---

## 5. Step 4: DAX

Create these in **Modeling, New table / New column / New measure**. Group the measures into display folders if you like (Properties pane, Display folder).

### 5.1 Calculated tables

**Settings** (one row: the single place to change the "as of" date)

```dax
Settings =
ROW (
    "AsOf",       DATE ( 2026, 11, 16 ),
    "ValidStart", DATE ( 2025, 1, 1 ),
    "StdHours",   7.5
)
```

For a live report, change `DATE ( 2026, 11, 16 )` to `TODAY ()`.

**What-if parameter tables** (disconnected; each feeds a slicer)

```dax
Field Engineer Count =
SELECTCOLUMNS ( GENERATESERIES ( 1, 40, 1 ), "Field Engineer Count", [Value] )

FE Availability =
SELECTCOLUMNS ( GENERATESERIES ( 0.5, 1, 0.05 ), "FE Availability", [Value] )

Travel Time =
SELECTCOLUMNS ( GENERATESERIES ( 0, 4, 0.25 ), "Travel Time", [Value] )

Seasonality Switch =
DATATABLE ( "Option", STRING, { { "On" }, { "Off" } } )
```

Format `FE Availability[FE Availability]` as percentage. Add each of the four as a slicer on the Capacity page (list or dropdown; use "Single select").

**MonthlyDemand** (history of due hours by month, all regions, used to estimate seasonality)

```dax
MonthlyDemand =
VAR _startYM =
    YEAR ( MAX ( Settings[ValidStart] ) ) * 12 + MONTH ( MAX ( Settings[ValidStart] ) )
VAR _endYM =
    YEAR ( MAX ( Settings[AsOf] ) ) * 12 + MONTH ( MAX ( Settings[AsOf] ) )
VAR _t =
    ADDCOLUMNS (
        SUMMARIZE ( 'Calendar', 'Calendar'[YearMonthNumber], 'Calendar'[MonthNo] ),
        "@H", CALCULATE ( SUM ( Jobs[EstimatedHours] ) )
    )
RETURN
    SELECTCOLUMNS (
        FILTER ( _t, 'Calendar'[YearMonthNumber] >= _startYM && 'Calendar'[YearMonthNumber] <= _endYM ),
        "YearMonthNumber", 'Calendar'[YearMonthNumber],
        "MonthNo", 'Calendar'[MonthNo],
        "Hours", [@H]
    )
```

**Seasonality** (twelve rows: the index for each calendar month)

```dax
Seasonality =
VAR _hist = ADDCOLUMNS ( MonthlyDemand, "X", [YearMonthNumber] )
VAR _mx   = AVERAGEX ( _hist, [X] )
VAR _my   = AVERAGEX ( _hist, [Hours] )
VAR _b    = DIVIDE ( SUMX ( _hist, ( [X] - _mx ) * ( [Hours] - _my ) ), SUMX ( _hist, ( [X] - _mx ) ^ 2 ) )
VAR _a    = _my - _b * _mx
VAR _ratios =
    ADDCOLUMNS ( _hist, "Ratio", DIVIDE ( [Hours], _a + _b * [X] ) )
VAR _raw =
    ADDCOLUMNS (
        GENERATESERIES ( 1, 12, 1 ),
        "Raw", AVERAGEX ( FILTER ( _ratios, [MonthNo] = [Value] ), [Ratio] )
    )
VAR _norm = AVERAGEX ( _raw, [Raw] )
RETURN
    SELECTCOLUMNS ( _raw, "MonthNo", [Value], "SeasonalIndex", DIVIDE ( [Raw], _norm ) )
```

How it works, in words:

1. Fit a straight line through monthly due hours (least squares).
2. For each month, divide the actual hours by the value on the line. Above 1.0 means the month ran hot.
3. Average those ratios by calendar month (all Januaries together, and so on).
4. Divide by the overall average of those twelve numbers so the index averages to 1.0.

This table is estimated across all regions to keep it stable. If you have enough history, add `Region` to `MonthlyDemand` and build a per-region version.

### 5.2 Calculated columns

On `Jobs`:

```dax
IsOverdue =
VAR _asof = MAX ( Settings[AsOf] )
RETURN
    Jobs[DueDate] < _asof
        && ( ISBLANK ( Jobs[ScheduledDate_Clean_DT] ) || INT ( Jobs[ScheduledDate_Clean_DT] ) > _asof )
        && Jobs[LifecycleStatus_Corrected] IN { "Open", "Assigned", "Assigned ANC" }
```

A job is overdue only if it was due in the past **and** has no visit booked on or before today. A late job that is already booked is a different queue.

```dax
DaysPastDue =
IF (
    Jobs[IsOverdue],
    DATEDIFF ( Jobs[DueDate], MAX ( Settings[AsOf] ), DAY ),
    BLANK ()
)

OverdueBucket =
SWITCH (
    TRUE (),
    ISBLANK ( Jobs[DaysPastDue] ), "Not overdue",
    Jobs[DaysPastDue] <= 14, "1-14 days",
    Jobs[DaysPastDue] <= 45, "15-45 days",
    Jobs[DaysPastDue] <= 90, "46-90 days",
    "90+ days"
)

OverdueBucketSort =
SWITCH (
    TRUE (),
    ISBLANK ( Jobs[DaysPastDue] ), 0,
    Jobs[DaysPastDue] <= 14, 1,
    Jobs[DaysPastDue] <= 45, 2,
    Jobs[DaysPastDue] <= 90, 3,
    4
)
```

Set `OverdueBucket` to sort by `OverdueBucketSort`.

> **Rule of thumb:** a column if you need it as an axis, filter or key; a measure if it must react to what the user selects.

### 5.3 Measures: counting and effort

Create a blank table called `_Measures` (**Enter data**, one empty column, then delete the column) to hold measures, or keep them on the tables noted.

```dax
AsOf = MAX ( Settings[AsOf] )

Due Jobs = COUNTROWS ( Jobs )

Due Hours = SUM ( Jobs[EstimatedHours] )

Closed Jobs =
CALCULATE ( COUNTROWS ( Jobs ), USERELATIONSHIP ( 'Calendar'[Date], Jobs[ClosedDate] ) )

Closed Hours =
CALCULATE ( SUM ( Jobs[EstimatedHours] ), USERELATIONSHIP ( 'Calendar'[Date], Jobs[ClosedDate] ) )

Overdue Jobs =
CALCULATE ( COUNTROWS ( Jobs ), Jobs[IsOverdue] = TRUE (), REMOVEFILTERS ( 'Calendar' ) )

Overdue Hours =
CALCULATE ( SUM ( Jobs[EstimatedHours] ), Jobs[IsOverdue] = TRUE (), REMOVEFILTERS ( 'Calendar' ) )

Pending Debriefs =
CALCULATE ( COUNTROWS ( Jobs ), Jobs[DebriefPending] = "Y" )

Assigned Not Confirmed (28d) =
VAR _asof = [AsOf]
RETURN
    CALCULATE (
        COUNTROWS ( Jobs ),
        Jobs[LifecycleStatus_Corrected] = "Assigned ANC",
        Jobs[ScheduledDate_Clean_DT] >= _asof + 1,
        Jobs[ScheduledDate_Clean_DT] < _asof + 29,
        REMOVEFILTERS ( 'Calendar' )
    )

Planned Hours = SUM ( PlannedVisits[PlannedHours] )
```

Why `REMOVEFILTERS ( 'Calendar' )` on the overdue measures: the overdue count is a snapshot as of today. Without it, putting the measure on a month axis would slice it by due month, which is a different question.

Why variables (`_asof`) before `CALCULATE`: a boolean filter inside `CALCULATE` cannot reference a measure directly, so the value is captured first.

### 5.4 Measures: capacity

```dax
Std Hours per FE per Day = MAX ( Settings[StdHours] )

FE Count Value  = SELECTEDVALUE ( 'Field Engineer Count'[Field Engineer Count], 3 )
FE Avail Value  = SELECTEDVALUE ( 'FE Availability'[FE Availability], 0.85 )
Travel Value    = SELECTEDVALUE ( 'Travel Time'[Travel Time], 1.5 )

Workdays =
CALCULATE ( COUNTROWS ( 'Calendar' ), 'Calendar'[IsWorkday] = TRUE () )

FE Capacity Hours =
VAR _effectiveHoursPerDay = MAX ( 0, [Std Hours per FE per Day] - [Travel Value] )
RETURN
    [Workdays] * _effectiveHoursPerDay * [FE Count Value] * [FE Avail Value]

Utilisation % = DIVIDE ( [Due Hours], [FE Capacity Hours] )

Capacity Gap = [FE Capacity Hours] - [Due Hours]
```

Capacity is: workdays in the month, times productive hours per day (7.5 less travel), times engineers, times the share of time they are actually available for maintenance after leave, training and projects. The three parameter slicers feed it, so a manager can test staffing scenarios live.

The `SELECTEDVALUE` defaults (3, 0.85, 1.5) apply when nothing is selected.

### 5.5 Measures: the long-term forecast

First, some helpers.

```dax
Valid Start YM =
YEAR ( MAX ( Settings[ValidStart] ) ) * 12 + MONTH ( MAX ( Settings[ValidStart] ) )

Current YM = YEAR ( [AsOf] ) * 12 + MONTH ( [AsOf] )

Months Ahead = SELECTEDVALUE ( 'Calendar'[YearMonthNumber] ) - [Current YM]

Season Idx =
LOOKUPVALUE (
    Seasonality[SeasonalIndex],
    Seasonality[MonthNo], SELECTEDVALUE ( 'Calendar'[MonthNo] ),
    1
)

Season Idx Applied =
IF ( SELECTEDVALUE ( 'Seasonality Switch'[Option], "On" ) = "On", [Season Idx], 1 )

Deseasonalised Hours = DIVIDE ( [Due Hours], [Season Idx Applied] )
```

Then the three signals. They are all computed on **deseasonalised** history, and anchored on the current month (the last month of actual data), so every future month shares the same level.

```dax
Level 3M =
VAR _cur = [Current YM]
VAR _start = [Valid Start YM]
RETURN
    CALCULATE (
        AVERAGEX (
            FILTER (
                ALL ( 'Calendar'[YearMonthNumber], 'Calendar'[MonthNo] ),
                'Calendar'[YearMonthNumber] > _cur - 3
                    && 'Calendar'[YearMonthNumber] <= _cur
                    && 'Calendar'[YearMonthNumber] >= _start
            ),
            [Deseasonalised Hours]
        ),
        REMOVEFILTERS ( 'Calendar' )
    )

Level 6M =
VAR _cur = [Current YM]
VAR _start = [Valid Start YM]
RETURN
    CALCULATE (
        AVERAGEX (
            FILTER (
                ALL ( 'Calendar'[YearMonthNumber], 'Calendar'[MonthNo] ),
                'Calendar'[YearMonthNumber] > _cur - 6
                    && 'Calendar'[YearMonthNumber] <= _cur
                    && 'Calendar'[YearMonthNumber] >= _start
            ),
            [Deseasonalised Hours]
        ),
        REMOVEFILTERS ( 'Calendar' )
    )

Trend Hours =
VAR _x = SELECTEDVALUE ( 'Calendar'[YearMonthNumber] )
VAR _cur = [Current YM]
VAR _start = [Valid Start YM]
VAR _hist =
    CALCULATETABLE (
        FILTER (
            ADDCOLUMNS (
                ALL ( 'Calendar'[YearMonthNumber], 'Calendar'[MonthNo] ),
                "@Y", [Deseasonalised Hours]
            ),
            'Calendar'[YearMonthNumber] >= _start && 'Calendar'[YearMonthNumber] <= _cur
        ),
        REMOVEFILTERS ( 'Calendar' )
    )
VAR _mx = AVERAGEX ( _hist, 'Calendar'[YearMonthNumber] )
VAR _my = AVERAGEX ( _hist, [@Y] )
VAR _b =
    DIVIDE (
        SUMX ( _hist, ( 'Calendar'[YearMonthNumber] - _mx ) * ( [@Y] - _my ) ),
        SUMX ( _hist, ( 'Calendar'[YearMonthNumber] - _mx ) ^ 2 )
    )
RETURN
    _my + _b * ( _x - _mx )
```

Why `REMOVEFILTERS ( 'Calendar' )` appears in each of these: when the measure is plotted on a month axis, the axis puts a filter on the Calendar table. Without removing it, the history iteration would see only the single axis month. In `Trend Hours`, the value `_x` (the month being forecast) is captured **before** the filters are removed.

Now the forecast itself:

```dax
Forecast Hours =
VAR _ahead = [Months Ahead]
VAR _model = DIVIDE ( [Level 3M] + [Level 6M] + [Trend Hours], 3 ) * [Season Idx Applied]
RETURN
    SWITCH ( TRUE (), _ahead <= 2, [Due Hours], _model )

Forecast Hours (Incl Planned) = [Forecast Hours] + [Planned Hours]
```

The logic:

| Months ahead | Demand used | Why |
|---|---|---|
| 0 to 2 | Actual due hours (plus planned visits) | The work is already in the system |
| 3 or more | Average of the 3-month level, 6-month level and linear trend, each deseasonalised, multiplied by that month's seasonal index | No single method is reliable this far out, and demand has an annual shape |

Finally, the **management adjustment** layer, kept separate from the model so the baseline is never silently overwritten:

```dax
Management Adjustment Hours =
CALCULATE (
    SUM ( ManagementAdjustments[Hours] ),
    TREATAS ( VALUES ( 'Calendar'[MonthStart] ), ManagementAdjustments[Month] )
)

Adjusted Forecast Hours =
[Forecast Hours (Incl Planned)] + [Management Adjustment Hours]

Adjusted Utilisation % = DIVIDE ( [Adjusted Forecast Hours], [FE Capacity Hours] )

Adjusted Capacity Gap = [FE Capacity Hours] - [Adjusted Forecast Hours]
```

`TREATAS` lets you match the adjustment table to the calendar on the first of the month without a physical relationship on a non-unique column.

### 5.6 Measures: rolling averages for the chart

These are the plain rolling averages of **actual** hours, for the "trend" lines on the capacity chart. They are not part of the forecast arithmetic above.

```dax
Hours 3M Avg =
VAR _end = MAX ( 'Calendar'[Date] )
RETURN
    IF (
        SELECTEDVALUE ( 'Calendar'[YearMonthNumber] ) <= [Current YM],
        DIVIDE (
            CALCULATE ( [Due Hours], DATESINPERIOD ( 'Calendar'[Date], _end, -3, MONTH ) ),
            CALCULATE ( DISTINCTCOUNT ( 'Calendar'[YearMonthNumber] ), DATESINPERIOD ( 'Calendar'[Date], _end, -3, MONTH ) )
        )
    )

Hours 6M Avg =
VAR _end = MAX ( 'Calendar'[Date] )
RETURN
    IF (
        SELECTEDVALUE ( 'Calendar'[YearMonthNumber] ) <= [Current YM],
        DIVIDE (
            CALCULATE ( [Due Hours], DATESINPERIOD ( 'Calendar'[Date], _end, -6, MONTH ) ),
            CALCULATE ( DISTINCTCOUNT ( 'Calendar'[YearMonthNumber] ), DATESINPERIOD ( 'Calendar'[Date], _end, -6, MONTH ) )
        )
    )

In Chart Window =
VAR _x = SELECTEDVALUE ( 'Calendar'[YearMonthNumber] )
RETURN
    IF ( _x >= [Current YM] - 10 && _x <= [Current YM] + 6, 1, 0 )
```

Use `In Chart Window` as a visual-level filter (`= 1`) so the capacity chart shows the last 11 months plus the next 6.

### 5.7 Measures: overdue gauge

```dax
Overdue Hours Target =
VAR _cur = [Current YM]
RETURN
    0.05 *
        CALCULATE (
            SUM ( Jobs[EstimatedHours] ),
            REMOVEFILTERS ( 'Calendar' ),
            'Calendar'[YearMonthNumber] = _cur
        )

Overdue Hours Amber Limit = [Overdue Hours Target] * 1.2
```

The target is 5% of this month's due hours, amber up to 20% above target, red beyond.

### 5.8 Measures: booking letters

```dax
Offers = COUNTROWS ( BookingLetters )

Follow-ups =
SUMX (
    BookingLetters,
    IF ( NOT ISBLANK ( BookingLetters[FollowUp1Date] ), 1, 0 )
        + IF ( NOT ISBLANK ( BookingLetters[FollowUp2Date] ), 1, 0 )
)

Confirmations =
CALCULATE ( COUNTROWS ( BookingLetters ), BookingLetters[ConfirmDate] <> BLANK () )

Confirmation Rate % = DIVIDE ( [Confirmations], [Offers] )

Avg Days to Confirm = AVERAGE ( BookingLetters[DaysToConfirm] )
```

### 5.9 Optional: month-end closure forecast

This is the "will we hit this month's target?" view. It needs two what-if parameter tables:

```dax
Completion Rate = SELECTCOLUMNS ( GENERATESERIES ( 0.5, 1, 0.05 ), "Completion Rate", [Value] )
Pull-through    = SELECTCOLUMNS ( GENERATESERIES ( 0, 1, 0.05 ), "Pull-through", [Value] )
```

```dax
Closed MTD =
VAR _cur = [Current YM]
RETURN CALCULATE ( COUNTROWS ( Jobs ), Jobs[ClosedYM] = _cur, REMOVEFILTERS ( 'Calendar' ) )

Scheduled Remaining =
VAR _cur = [Current YM]
VAR _asof = [AsOf]
RETURN
    CALCULATE (
        COUNTROWS ( Jobs ),
        Jobs[ScheduledYM] = _cur,
        Jobs[ScheduledDate_Clean_DT] >= _asof + 1,
        Jobs[LifecycleStatus_Corrected] IN { "Assigned", "Assigned ANC" },
        REMOVEFILTERS ( 'Calendar' )
    )

Due Not Scheduled =
VAR _cur = [Current YM]
RETURN
    CALCULATE (
        COUNTROWS ( Jobs ),
        Jobs[DueYM] = _cur,
        Jobs[LifecycleStatus_Corrected] = "Open",
        ISBLANK ( Jobs[ScheduledDate_Clean_DT] ),
        REMOVEFILTERS ( 'Calendar' )
    )

Remaining Business Days =
VAR _asof = [AsOf]
RETURN
    CALCULATE (
        COUNTROWS ( 'Calendar' ),
        'Calendar'[IsWorkday] = TRUE (),
        'Calendar'[Date] > _asof,
        'Calendar'[Date] <= EOMONTH ( _asof, 0 ),
        REMOVEFILTERS ( 'Calendar' )
    )

Elapsed Business Days =
VAR _asof = [AsOf]
RETURN
    CALCULATE (
        COUNTROWS ( 'Calendar' ),
        'Calendar'[IsWorkday] = TRUE (),
        'Calendar'[Date] >= DATE ( YEAR ( _asof ), MONTH ( _asof ), 1 ),
        'Calendar'[Date] <= _asof,
        REMOVEFILTERS ( 'Calendar' )
    )

Avg Daily Closures = DIVIDE ( [Closed MTD], [Elapsed Business Days] )

Month-end Forecast =
[Closed MTD]
    + [Scheduled Remaining] * SELECTEDVALUE ( 'Completion Rate'[Completion Rate], 0.9 )
    + MIN (
        [Due Not Scheduled],
        [Remaining Business Days] * [Avg Daily Closures]
            * SELECTEDVALUE ( 'Pull-through'[Pull-through], 0.25 )
    )
```

In words: closed so far, plus the booked work you expect to complete, plus the share of unbooked due work you can realistically pull in over the remaining days (capped at what is actually due).

---

## 6. Step 5: Check your numbers

Before building any visuals, confirm the model returns the same numbers as the sample data. Build a quick table or card for each. If yours differ, jump to troubleshooting.

| Check | Expected |
|---|---|
| Rows loaded: Jobs / WorkOrders / Sites / PlannedVisits / BookingLetters | 3,757 / 1,731 / 33 / 260 / 600 |
| Jobs by `LifecycleStatus_Corrected` | Closed 2,684, Open 707, Assigned 281, Assigned ANC 85 |
| Jobs whose status the work order corrected | 25 |
| Due Hours, Nov 2026, all regions | 342.3 |
| Due Hours, Oct 2026 / Jan 2025 | 318.9 / 269.2 |
| Overdue Jobs / Overdue Hours (as of 16 Nov 2026) | 181 / 444.1 |
| Closed Jobs, Oct 2026 | 90 |
| Pending Debriefs / Assigned Not Confirmed (28d) | 18 / 20 |
| Workdays, Nov 2026 | 21 |
| FE Capacity Hours, Nov 2026 (3 engineers, 85%, 1.5 h travel) | 321.3 (utilisation 106.5%) |
| Seasonal index, Jan to Dec | 0.950, 0.864, 1.047, 1.017, 0.999, 1.017, 1.054, 0.994, 0.994, 1.025, 1.050, 0.990 |
| Forecast Hours, Feb 2027 (all regions), seasonality On / Off, before planned hours and adjustments | 277.7 / 329.0 |
| Forecast Hours, Mar 2027, On / Off | 337.1 / 329.7 |

The last two rows are a good sanity test for the whole forecast: with seasonality off the forecast is nearly flat month to month, and with it on, February (a quiet month) dips and March (a busy month) rises.

---

## 7. Step 6: Build the pages

Use a 16:9 canvas. Set the **page background** to a light neutral and give every visual a white background with a small border radius so it reads as a card.

### Page 1: Operations

Slicers across the top: `Regions[Region]` and `Modalities[Modality]` (dropdowns). Sync them to other pages (View, Sync slicers).

| Visual | Fields | Notes |
|---|---|---|
| Card x4 | Overdue Jobs, Overdue Hours, Pending Debriefs, Assigned Not Confirmed (28d) | Turn the category label off, use the title instead |
| Map (bubble) | Location: Sites[City]; Latitude: Sites[Latitude]; Longitude: Sites[Longitude]; Size: Overdue Jobs; Legend (optional): LifecycleStatus_Corrected | The map visual may need to be enabled by your administrator. If it is blocked, use a scatter chart with Longitude on X and Latitude on Y, which gives a recognisable layout for synthetic data |
| Matrix (heatmap) | Rows: Jobs[LifecycleStatus_Corrected]; Columns: Jobs[OverdueBucket]; Values: Overdue Jobs | **Cell elements, Background colour, Gradient** from light to dark in one hue. Turn on row and column totals off. Filter out "Not overdue" with a visual filter |
| Bar chart | Axis: Regions[Region]; Values: Overdue Jobs | Sort descending |
| Gauge | Value: Overdue Hours; Target value: Overdue Hours Target; Maximum value: Overdue Hours Amber Limit | Add a conditional rule on the data colour: green if Overdue Hours <= Target, amber if <= Amber Limit, red otherwise |
| Table | Job ID, Site, Modality, Region, Days Past Due, Status | Visual filter: IsOverdue = TRUE. Sort by Days Past Due descending, Top N 25 |

### Page 2: Capacity and Forecast

Slicers: the four parameter tables (Field Engineer Count, FE Availability, Travel Time, Seasonality Switch), plus Regions and Modalities.

| Visual | Fields | Notes |
|---|---|---|
| Cards | FE Capacity Hours, Adjusted Forecast Hours, Adjusted Utilisation %, Overdue Hours | Filter the card context to the current month if needed using a visual filter on Calendar[YearMonthNumber] |
| Clustered column | Axis: Calendar[YearMonth]; Values: FE Capacity Hours, Adjusted Forecast Hours | Visual filter: In Chart Window = 1. Make forecast months lighter if you like with a rule on `Months Ahead > 0` |
| Line chart | Axis: Calendar[YearMonth]; Values: Hours 3M Avg, Hours 6M Avg, Forecast Hours (Incl Planned) | Same filter. Use dashed lines for the averages |
| Line chart | Axis: Calendar[YearMonth]; Value: Adjusted Utilisation % | Add a constant line at 1.0 (Analytics pane, Constant line) |
| Column chart | Axis: Calendar[YearMonth]; Value: Adjusted Capacity Gap | Colour by sign with a conditional rule: green positive, orange negative |
| Column chart | Axis: Seasonality[MonthNo]; Value: Seasonality[SeasonalIndex] | Shows the fitted annual shape. Add a constant line at 1.0 |
| Gauge | Value: Adjusted Utilisation % for next month; Target: 1; Maximum: 1.2 | |
| Table | Month, Region, Modality, Hours, Reason, ApprovedBy from ManagementAdjustments | The audit trail for the human judgement |

**Important:** put one measure per axis. Do not use a secondary axis to combine hours and percentages. Two scales on one chart mislead; use separate charts, as above.

### Page 3: Booking Letters

| Visual | Fields | Notes |
|---|---|---|
| Cards | Offers, Confirmation Rate %, Avg Days to Confirm | |
| Funnel | Offers, Follow-ups, Confirmations | Add the three as separate measures in the Values well |
| Line chart | Axis: Calendar[YearMonth]; Values: Offers, Follow-ups, Confirmations | |
| Clustered column | Axis: BookingLetters[Planner]; Values: Follow-ups | |
| Donut | Legend: BookingLetters[Outcome]; Values: Offers | |

---

## 8. Step 7: The management adjustments workflow

This is the part that makes the forecast usable in a real planning conversation.

A statistical model only knows what is in the data. Installs are agreed in meetings. Leave is approved after the model refreshes. A contract change or a decision to pull work forward can move a month by hundreds of hours.

1. Open `management_adjustments.csv` in Excel (or a text editor).
2. Add a row per decision: the first of the month, region, modality, hours to add (positive) or remove (negative), a short reason, and who approved it.
3. Save the file and click **Refresh** in Power BI.
4. The capacity chart's demand bar moves, utilisation and gap update, and the audit table shows the change.

Rules that keep this honest:

- **Never overwrite the model.** The statistical forecast and the adjustment stay in separate measures, so anyone can see which part is the model and which is judgement.
- **Every adjustment has a reason and an approver.** If it cannot be explained in one line, it should not be in the forecast.
- **Review and clear old rows** each quarter so stale decisions do not linger.

---

## 9. Step 8: Polish and share

**Theme.** Save this as `theme.json`, then **View, Themes, Browse for themes**. It gives you a restrained, colourblind-friendly categorical set and neutral surfaces.

```json
{
  "name": "Maintenance Planning",
  "dataColors": ["#2F7DD7", "#E8693A", "#1FB07A", "#E8A100", "#E87BA5", "#6B7280"],
  "background": "#FCFCFB",
  "foreground": "#0B0B0B",
  "tableAccent": "#2F7DD7",
  "textClasses": {
    "title": { "fontFace": "Segoe UI Semibold", "fontSize": 12, "color": "#0B0B0B" },
    "label": { "fontFace": "Segoe UI", "fontSize": 10, "color": "#52514E" }
  },
  "visualStyles": {
    "*": { "*": { "border": [{ "show": true, "color": { "solid": { "color": "#E3E2DC" } }, "radius": 8 }] } }
  }
}
```

**Design rules worth following**

- One measure per axis. No dual axes.
- Use a status colour with an icon or label (for example a down-arrow and the word "over"), never colour alone.
- Keep colour meaning stable: Region 1 is always the same colour on every chart.
- Dashed lines for derived series (averages), solid for actuals.
- Lighter fill for forecast months, full colour for actuals.
- Title every visual with the question it answers, not just the measure name.

**Sharing for a portfolio**

- Export each page as an image (File, Export, or use Windows Snipping Tool) and add them to your repository or write-up.
- Record a short screen capture of the sliders and slicers moving. A 20-second clip shows interactivity better than any screenshot.
- Be careful with **Publish to web**. It makes a report public to anyone with the link. Only do this from a personal account, with synthetic data, and never from a work tenant.
- If you cannot publish a live report, a web version built in plain HTML, CSS and JavaScript runs anywhere with no licence at all.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `DataSource.NotFound` on refresh | `DataFolder` points to the wrong folder | Edit the parameter (Home, Transform data, Manage parameters) |
| Dates show as errors in `Jobs` | Locale mismatch | Keep the `"en-AU"` argument on `Table.TransformColumnTypes`, or change it to your locale. The CSVs use ISO dates (`yyyy-mm-dd`), which parse in any locale |
| `Expression.Error: The column 'WorkOrderStatus' ...` | `WorkOrders` query missing or renamed | Names must match exactly |
| Row count in `Jobs` is higher than 3,757 | Duplicate work orders caused the merge to multiply rows | Keep `Table.Distinct` in `WorkOrders` |
| "A circular dependency was detected" | `MonthlyDemand` or `Seasonality` referencing a measure that depends on them | Use the calculated tables exactly as written; they only reference `Calendar`, `Jobs` and `Settings` |
| Month axis shows only one point on the forecast lines | Filters not removed inside the trend measures | Check the `REMOVEFILTERS ( 'Calendar' )` wrapper is present |
| Seasonal index all 1.0 | `Seasonality Switch` set to Off, or `MonthlyDemand` has under 12 months | Set the switch to On; confirm `Settings[ValidStart]` and `[AsOf]` |
| Ambiguous relationship warning | `Regions` related to `Sites` as well as to the fact tables | Remove the `Regions` to `Sites` relationship |
| Capacity looks wrong | Wrong parameter selected in a slicer | Clear the slicers, or check the `SELECTEDVALUE` defaults |
| Overdue count is zero | `Settings[AsOf]` is earlier than the data | Set `AsOf` to `DATE ( 2026, 11, 16 )` for the sample data |
| Map is blank | Map visual disabled, or Latitude/Longitude not categorised | Enable the visual, or set Data category on those columns (Column tools). Fall back to a scatter chart |

---

## 11. Using your own data

The sample files mirror the shape of a typical maintenance or work-order export. To adapt:

1. Map your system's columns to the dictionary in section 1. You need at minimum: a job ID, a due date, an estimated duration, a status, a scheduled date (or blank), a closed date, and a region or team.
2. Keep the column names in the sample CSVs, or edit the `Table.TransformColumnTypes` lists in section 3 to match yours.
3. Anything your source stores as text but you need as a date, clean it in Power Query exactly as `ScheduledDate_Clean_DT` does: trim it, treat placeholders as null, use `try ... otherwise null`.
4. If your status is stored in more than one system, decide which is the source of truth and merge it in (as `WorkOrders` does).
5. Keep at least **18 to 24 months** of valid history if you want the seasonal index to be meaningful. Set `Settings[ValidStart]` to the first month you trust.
6. **Never load real customer, patient or personal data into a shared or published file.** Replace names, sites and identifiers with synthetic values first. The generator in `scripts/generate_sample_data.py` shows one way to build realistic stand-ins.

### Regenerating the sample data

```
python scripts/generate_sample_data.py sample-data
```

The generator is seeded, so it produces the same files every time. Change `SEED`, the region list, the seasonal pattern (`SEAS`) or the volumes to build a different dataset.

---

## 12. Concepts to take away

- **Clean in Power Query, calculate in DAX.** Type fixing, parsing and merges belong in M. Anything that depends on a slicer or a date belongs in DAX.
- **One source of truth per fact.** The corrected status comes from the work order, and the "as of" date comes from one table.
- **Separate the model from the judgement.** A forecast plus a visible, approved adjustment is more trustworthy than a single blended number.
- **Use the right horizon for the right data.** Actuals for the next two months, a blended and seasonally adjusted model beyond that.
- **Test with checkpoint numbers.** Known answers on known data catch model errors before the visuals hide them.
