# The dashboard

## What it shows

- **Now:** charge gauge with low and high zones, range, a status chip (Charging, Driving, Parked) that pulses while active, energy and cost so far when charging, lock and service badges, last position with its age. Charging notes appear only for exceptions: plugged in but not charging, a charger fault, or charging at a peak rate while a cheaper window is coming.
- **Report:** a weekly "hot takes" summary written by Claude from the week's aggregated stats, with the numbers behind it.
- **Trips:** distance per day, estimated consumption (kWh/100 km) and a table. A trip is one driving window.
- **Charging:** sessions with a power curve, energy and cost by location, "could have saved" (peak-rate energy priced at the cheapest rate), a warning when sessions run below the car's amp limit, and the car's charging settings (target charge, amp limit, timers, read-only). If a session's estimated cost is wrong (a different tariff, a fee, a free charge), open "Set the cost manually" under the chart, unlock it with the admin password (15 minutes per browser session, five wrong guesses lock everyone out for a minute) and enter what you paid; it replaces the estimate in the dashboard and in the weekly report, and "Use estimate" brings the estimate back. Corrections are stored in the `session_costs` table, keyed by the session's start time.
- **Battery:** state of charge over time with charging sessions shaded, and SoC lost while parked.
- **Insights:** real-world range, energy per full charge, cost per 100 km, optional CO2, a day-by-hour grid of driving and charging, and a monthly table.
- **Air:** cabin air quality index and PM2.5 over time, a daily summary and pre-cleaning cycles. The API documents neither the index scale nor the PM2.5 unit.
- **Data:** snapshot counts and freshness per domain, and the latest raw payload.

The **Period** selector in the sidebar filters Trips, Charging, Battery, Insights and Air. Now, Report and Data always show the latest.

## How the data behaves

- Telemetry is **event-driven**: the car pushes an update only when a value changes. The battery reports every few minutes while charging. A poll sees only the latest event, so a shorter interval captures more of them.
- **While driving** the car sends battery events but no position, and the odometer only when a drive starts and when it ends. Trips are therefore the car's driving windows (from usage-mode events) with the distance taken from the odometer at both ends. There is no route to draw, only where the car parked.
- Energy figures are **estimates**: trips use the SoC change times `BATTERY_KWH`, and charge sessions use reported power over time. This car's API exposes no energy counters. Trips under 3 km get no energy estimate because SoC moves in 1 % steps.
- A charge session whose start was not observed (it began before tracking, or after a long gap) is marked "since tracking began" and its energy is a lower bound.
- History starts at the first poll. There is no backfill.
- The dashboard shows your location. Reading is open unless you set `DASHBOARD_PASSWORD` (see [Access and the login page](deployment.md#access-and-the-login-page)); editing always needs the admin password. Keep it on localhost or a private network such as Tailscale.
