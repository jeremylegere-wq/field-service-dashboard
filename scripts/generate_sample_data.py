"""Generates fully synthetic sample data for the Maintenance Planning Power BI tutorial.
No real customers, sites, people or volumes. Re-run with a different SEED for a different dataset.
Usage: python generate_sample_data.py [output_folder]
"""
import csv, random, sys, math
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 42
ASOF = date(2026, 11, 16)           # the "today" the sample data is built around
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "sample-data")
OUT.mkdir(parents=True, exist_ok=True)
R = random.Random(SEED)

REGIONS = ["East", "South", "North", "Central", "West", "Island"]
REG_W   = [.27, .22, .20, .07, .10, .14]
MODS    = ["CT", "MR", "X-Ray", "Ultrasound"]
MOD_W   = [.30, .22, .28, .20]
DUR     = {"CT": 150, "MR": 210, "X-Ray": 90, "Ultrasound": 120}      # minutes
SEAS    = [.93, .91, 1.04, 1.0, 1.02, 1.04, 1.05, 1.0, .98, 1.04, 1.03, .97]   # Jan..Dec
CITIES  = {"East": [("Port Harlow", -33.87, 151.21), ("Kestrel Bay", -32.93, 151.78)],
           "South": [("Fairhaven", -37.81, 144.96), ("Thornbury", -38.15, 144.36)],
           "North": [("Northgate", -27.47, 153.03), ("Cape Verity", -19.26, 146.82)],
           "Central": [("Westmere", -34.93, 138.60)],
           "West": [("Sandford", -31.95, 115.86), ("Quarry Bay", -33.33, 115.64)],
           "Island": [("Harbour City", -36.85, 174.76), ("Rivermouth", -43.53, 172.64)]}
STEMS = ["Riverbend","Hillcrest","Bayview","Summit","Parkside","Lakeside","Ridgeway","Meadowbrook",
         "Cedar Park","Westfield","Oakdale","Stonebridge","Fairview","Kingsway","Marlow","Eastgate"]
TYPES = ["Medical Centre","Regional Hospital","Imaging Clinic","Community Hospital","Diagnostic Centre"]
PEOPLE = ["Alex","Bianca","Callum","Dani","Elias","Farah","Gus","Hana"]
PLANNERS = ["Alex","Bianca","Callum","Dani"]

def wpick(items, w): return R.choices(items, weights=w, k=1)[0]
def is_biz(d): return d.weekday() < 5
def rand_biz(a, b):
    while True:
        d = a + timedelta(days=R.randint(0, (b - a).days))
        if is_biz(d): return d
def w(path, header, rows):
    with open(OUT / path, "w", newline="", encoding="utf-8") as f:
        cw = csv.writer(f); cw.writerow(header); cw.writerows(rows)

# ---------- sites ----------
sites, used = [], set()
for reg in REGIONS:
    for city, la, lo in CITIES[reg]:
        for _ in range(3):
            while True:
                nm = f"{R.choice(STEMS)} {R.choice(TYPES)}"
                if nm not in used: used.add(nm); break
            sites.append(("S%03d" % (len(sites) + 1), nm, reg, city, round(la + R.uniform(-.15, .15), 4), round(lo + R.uniform(-.15, .15), 4)))
w("sites.csv", ["SiteID", "SiteName", "Region", "City", "Latitude", "Longitude"], sites)
by_reg = {r: [s for s in sites if s[2] == r] for r in REGIONS}

# ---------- jobs + work orders ----------
START, END = date(2025, 1, 1), date(2027, 5, 31)
jobs, wos, jid, woid = [], [], 100001, 50001
months = []
y, m = START.year, START.month
while date(y, m, 1) <= END:
    months.append((y, m)); m += 1
    if m == 13: y, m = y + 1, 1
for t, (y, m) in enumerate(months):
    n = round((120 + 0.7 * t) * SEAS[m - 1] * (1 + .04 * math.sin(t * 2.3)))
    first = date(y, m, 1); last = (date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1))
    for _ in range(n):
        reg = wpick(REGIONS, REG_W); site = R.choice(by_reg[reg]); mod = wpick(MODS, MOD_W)
        due = rand_biz(first, last)
        dur = int(DUR[mod] * R.uniform(.85, 1.2) // 5 * 5)
        status, sched, closed, deb, assignee, wo = "Open", "-", "", "N", "", ""
        if due < ASOF:
            age = (ASOF - due).days
            p_stay = 0.02 if age > 180 else 0.07 if age > 60 else 0.30 if age > 14 else .55
            lag = R.randint(-12, 28)
            cd = due + timedelta(days=lag)
            if R.random() > p_stay and cd <= ASOF:
                status, closed = "Closed", cd.isoformat()
                if (ASOF - cd).days <= 30 and R.random() < .25: deb = "Y"
                assignee = R.choice(PEOPLE)
            else:
                r = R.random()
                if r < .15:   # booked, in the future
                    status = wpick(["Assigned", "Assigned ANC"], [.7, .3]); sched = (ASOF + timedelta(days=R.randint(1, 21))).isoformat() + " 08:00"; assignee = R.choice(PEOPLE)
                elif r < .30: status = "Open"
                else:         status = "Open"
        else:
            r = R.random()
            if r < .40:
                status = wpick(["Assigned", "Assigned ANC"], [.75, .25])
                sd = due + timedelta(days=R.randint(-10, 3)); sd = max(sd, ASOF + timedelta(days=1))
                while not is_biz(sd): sd += timedelta(days=1)
                sched = sd.isoformat() + " " + R.choice(["08:00", "09:30", "13:00"]); assignee = R.choice(PEOPLE)
        if status in ("Assigned", "Assigned ANC") or (status == "Closed" and R.random() < .5):
            wo = f"WO-{woid}"; woid += 1
            ws = status
            if status == "Assigned" and R.random() < .08: ws = "Assigned"      # work order is the source of truth
            wos.append((wo, ws))
            if R.random() < .10 and status == "Assigned": status = "Open"      # stale job status the M query corrects
        jobs.append((f"MJ-{jid}", site[0], reg, mod, due.isoformat(), dur, status, sched, closed, assignee, wo, deb)); jid += 1
w("jobs.csv", ["JobID", "SiteID", "Region", "Modality", "DueDate", "EstimatedDurationMin", "LifecycleStatus", "ScheduledDateTime", "ClosedDate", "AssignedTo", "AssignmentLeader", "DebriefPending"], jobs)
w("work_orders.csv", ["WorkOrderNumber", "WorkOrderStatus"], wos)

# ---------- planned visits (planning tool output, next 120 days) ----------
pv = []
for i in range(260):
    reg = wpick(REGIONS, REG_W); site = R.choice(by_reg[reg]); mod = wpick(MODS, MOD_W)
    d = rand_biz(ASOF + timedelta(days=1), ASOF + timedelta(days=120))
    pv.append((f"PP-{40000 + i}", site[0], reg, mod, d.isoformat(), int(DUR[mod] * R.uniform(.9, 1.15) // 5 * 5), R.choice([30, 45, 60, 75, 90]),
               "Y" if R.random() < .78 else "N", "Y" if R.random() < .6 else "N"))
w("planned_visits.csv", ["PlanID", "SiteID", "Region", "Modality", "PlannedDate", "DurationMin", "TravelMin", "CustomerContacted", "Locked"], pv)

# ---------- booking letter journeys ----------
bl = []
for i in range(600):
    reg = wpick(REGIONS, REG_W); mod = wpick(MODS, MOD_W); pl = wpick(PLANNERS, [.3, .3, .25, .15])
    off = ASOF - timedelta(days=R.randint(0, 120))
    while not is_biz(off): off -= timedelta(days=1)
    age = (ASOF - off).days
    conf = R.randint(1, 28) if R.random() < (.93 if age > 25 else .62) else None
    f1 = f2 = None; day = R.randint(4, 9)
    while (conf is None or day < conf) and day <= age and (f1 is None or f2 is None):
        if f1 is None: f1 = day
        else: f2 = day
        day += R.randint(4, 9)
    cdt = off + timedelta(days=conf) if conf and conf <= age else None
    outcome = ("Confirmed first time" if f1 is None else "Confirmed after follow-up") if cdt else ("Cancellation due" if age > 20 else "Open")
    bl.append((f"BJ-{7000 + i}", pl, reg, mod, off.isoformat(), (off + timedelta(days=f1)).isoformat() if f1 else "", (off + timedelta(days=f2)).isoformat() if f2 else "", cdt.isoformat() if cdt else "", outcome))
w("booking_letters.csv", ["JourneyID", "Planner", "Region", "Modality", "OfferDate", "FollowUp1Date", "FollowUp2Date", "ConfirmDate", "Outcome"], bl)

# ---------- management adjustments (governed input table) ----------
adj = [("2026-12-01", "East", "CT", 40, "Planned install, engineers diverted", "Regional Manager"),
       ("2027-01-01", "East", "CT", 25, "Planned install, engineers diverted", "Regional Manager"),
       ("2027-02-01", "South", "MR", -30, "Contract change, fewer visits", "Service Director"),
       ("2027-03-01", "North", "X-Ray", 35, "Pull-forward of low-priority work approved", "Regional Manager"),
       ("2027-03-01", "Island", "Ultrasound", -20, "Engineer lent to another region", "Service Director")]
w("management_adjustments.csv", ["Month", "Region", "Modality", "Hours", "Reason", "ApprovedBy"], adj)
print("rows:", {"jobs": len(jobs), "work_orders": len(wos), "planned": len(pv), "letters": len(bl), "sites": len(sites)})
