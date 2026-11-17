"""Build a synthetic telemetry database for screenshots and demos (no real data, made-up places).

    uv run python demo/make_demo_db.py data/demo.db          # then: DB_PATH=data/demo.db CONFIG_PATH=demo/config.toml mise run app

The last event is a charging session in progress at the office car park, ending a few minutes before now."""
import datetime as dt
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polestar import report, stats  # noqa: E402
from polestar.collect import connect, store  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
VIN = "DEMOVIN0000000000"
BATTERY_KWH = 78
HOME, OFFICE, FAST = (57.6950, 11.9400), (57.7089, 11.9746), (58.2840, 12.2900)
DAYS = 75
rnd = random.Random(46)


class Car:
    def __init__(self, now):
        self.now, self.db = now, None
        self.soc, self.odo, self.pos = 64.0, 18_400_000, HOME   # odometer in metres
        self.ev = []

    def add(self, ts, domain, payload):
        self.ev.append((ts, domain, {**payload, "timestamp": {"seconds": int(ts)}, "vin": VIN,
                                     "metaEventId": f"{domain}-{len(self.ev)}"}))

    def battery(self, ts, charging=None, kw=0.0, plugged=False):
        ctype = {"ac": "CHARGING_TYPE_AC", "dc": "CHARGING_TYPE_DC"}.get(charging)
        self.add(ts, "battery", {
            "batteryChargeLevelPercentage": round(self.soc),
            "estimatedDistanceToEmptyKm": round(self.soc * 4.45),
            "chargingStatusV2": "CHARGING_STATUS_V2_CHARGING" if charging else "CHARGING_STATUS_V2_IDLE",
            "chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_CONNECTED" if (charging or plugged) else "CHARGER_CONNECTION_STATUS_DISCONNECTED",
            **({"chargingType": ctype, "chargingPowerWatts": round(kw * 1000), "chargingCurrentAmps": 16 if charging == "ac" else 180} if charging else {}),
        })

    def place(self, ts, pos):
        self.pos = pos
        self.add(ts, "location", {"coordinate": {"latitude": pos[0] + rnd.uniform(-2e-4, 2e-4),
                                                  "longitude": pos[1] + rnd.uniform(-2e-4, 2e-4)}})

    def drive(self, start, km, dest):
        km *= rnd.uniform(0.95, 1.06)
        minutes = km / rnd.uniform(32, 70) * 60 + 4
        end = start + minutes * 60
        self.add(start - 30, "odometer", {"odometerMeters": self.odo})
        self.add(start, "availability", {"usageMode": "USAGE_MODE_DRIVING", "availabilityStatus": "AVAILABILITY_STATUS_AVAILABLE"})
        self.battery(start)
        self.soc -= km * rnd.uniform(0.2, 0.27)
        self.odo += int(km * 1000)
        self.add(end - 20, "odometer", {"odometerMeters": self.odo})
        self.battery(end - 10)
        self.add(end, "availability", {"usageMode": "USAGE_MODE_INACTIVE", "availabilityStatus": "AVAILABILITY_STATUS_AVAILABLE"})
        self.place(end + 30, dest)
        return end + 60

    def charge(self, start, target, kind, max_minutes=600, close=True):
        self.battery(start - 120, plugged=False)
        ts, n = start, 0
        while self.soc < target and n * 5 < max_minutes and ts < self.now - 90:
            if kind == "ac":
                kw = rnd.uniform(10.6, 11.0)
            else:
                kw = max(32.0, 118 - max(0.0, self.soc - 20) * 1.6) * rnd.uniform(0.97, 1.0)
            self.battery(ts, kind, kw)
            self.soc = min(100.0, self.soc + kw * (5 / 60) / BATTERY_KWH * 100 * 0.93)
            ts += 300 if kind == "ac" else 120
            n += 1
        if close:
            self.battery(ts, plugged=True)
        return ts + 60

    def idle(self, ts):
        self.soc = max(5.0, self.soc - rnd.uniform(0.0, 0.6))
        self.battery(ts)


def at(day, hhmm, jitter=15):
    h, m = map(int, hhmm.split(":"))
    return (datetime(day.year, day.month, day.day, h, m, tzinfo=TZ) + timedelta(minutes=rnd.uniform(-jitter, jitter))).timestamp()


def build(now):
    car = Car(now)
    today = datetime.fromtimestamp(now, TZ).date()
    for back in range(DAYS, 0, -1):
        day = today - timedelta(days=back)
        wd = day.weekday()
        t = at(day, "06:30", 0)
        car.place(t, car.pos)
        car.add(t, "exterior", {"centralLock": "LOCK_STATUS_LOCKED", "frontLeftDoor": "OPEN_STATUS_CLOSED", "hood": "OPEN_STATUS_CLOSED", "tailgate": "OPEN_STATUS_CLOSED"})
        car.idle(t)
        if wd < 5:
            t = car.drive(at(day, "07:50"), rnd.uniform(15, 21), OFFICE)
            if car.soc < 62 and rnd.random() < 0.6:               # top up at the office car park
                t = car.charge(t + 300, min(85, car.soc + 22), "ac", 150)
            car.idle(at(day, "12:30"))
            t = car.drive(at(day, "17:30"), rnd.uniform(15, 21), HOME)
            if car.soc < 60 and rnd.random() < 0.35:              # plugged in right away: peak rate
                t = car.charge(t + 300, 80, "ac")
            if rnd.random() < 0.3:
                t = car.drive(at(day, "19:30"), rnd.uniform(4, 11), (57.6800, 11.9300))
                t = car.drive(t + 2400, rnd.uniform(4, 11), HOME)
        elif wd == 5:
            t = car.drive(at(day, "11:00", 40), rnd.uniform(6, 14), (57.7000, 11.9600))
            t = car.drive(t + 5400, rnd.uniform(6, 14), HOME)
        elif rnd.random() < 0.45:
            t = car.drive(at(day, "10:00", 30), rnd.uniform(95, 150), FAST)
            if car.soc < 55:
                t = car.charge(t + 300, 80, "dc", 60)
            t = car.drive(max(t, at(day, "15:30", 20)), rnd.uniform(95, 150), HOME)
        t = at(day, "22:30", 10)
        car.battery(t, plugged=car.soc < 55)
        if car.soc < 58:                                          # cheap night window, 80 % target
            car.charge(at(day, "23:05", 3), 80, "ac")
        car.add(at(day, "06:00", 10) + 86400 * 0, "health", {"daysToService": 214 - back // 3, "distanceToServiceKm": 6200 - back * 30})
        for hh in ("09:00", "15:00", "21:00"):
            car.add(at(day, hh), "preCleaning", {"measuredAirQualityIndex": rnd.choice([12, 14, 18, 22, 31, 45]),
                                                 "measuredParticulateMatter25": rnd.choice([3, 4, 5, 8, 12, 19]),
                                                 "measurementDate": {"seconds": int(at(day, hh))},
                                                 "lastCycleCompleted": {"seconds": int(at(day, "08:00", 0))},
                                                 "lastCycleValid": True})

    # today: commute, then charging at the office car park, in progress
    t0 = now - 100 * 60
    car.place(t0 - 60, HOME)
    car.add(t0 - 60, "exterior", {"centralLock": "LOCK_STATUS_LOCKED", "frontLeftDoor": "OPEN_STATUS_CLOSED", "hood": "OPEN_STATUS_CLOSED", "tailgate": "OPEN_STATUS_CLOSED"})
    car.soc = min(car.soc, 41.0)
    t = car.drive(t0, 18.5, OFFICE)
    car.battery(t + 120, plugged=False)
    car.charge(t + 300, 90, "ac", close=False)
    car.add(now - 3600, "health", {"daysToService": 192, "distanceToServiceKm": 5400})
    car.add(now - 600, "preCleaning", {"measuredAirQualityIndex": 14, "measuredParticulateMatter25": 4,
                                       "measurementDate": {"seconds": int(now - 600)}, "lastCycleValid": True,
                                       "lastCycleCompleted": {"seconds": int(now - 7200)}})
    off = 60
    tz = {"offsetMinutes": off}
    car.add(now - 86400 * 30, "targetSoc", {"targetSoc": {"batteryChargeTargetLevel": 80}})
    car.add(now - 86400 * 30, "ampLimit", {"ampLimit": {"ampLimit": 16}})
    car.add(now - 86400 * 30, "globalChargeTimer", {"globalChargeTimer": {"activated": True, "start": {"hour": 23, "timeZone": tz}, "stop": {"hour": 6, "timeZone": tz}}})
    car.add(now - 86400 * 30, "parkingClimateTimer", {"parkingClimateTimers": [
        {"index": 0, "activated": True, "weekdays": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"], "readyAt": {"hour": 7, "minute": 40, "timeZone": tz}}]})
    return car.ev


def sample_report(w):
    """A hand-written stand-in for the weekly Claude report, filled with the week's own (synthetic) figures."""
    d, pd_, c, pc, b = w["driving"], w["previous_week_driving"], w["charging"], w["previous_week_charging"], w["battery"]
    delta = d["km"] - pd_["km"]
    loc = max(c["by_location"], key=lambda x: x["kwh"]) if c["by_location"] else None
    bullets = [
        f"**{d['trips']} trips, {d['km']:.0f} km**, {'up' if delta >= 0 else 'down'} {abs(delta):.0f} km on last week's {pd_['km']:.0f}. "
        f"The longest single drive was {d['longest_trip_km']:.0f} km, so the week was mostly short hops.",
        f"**Consumption of about {d['kwh_per_100km_estimated']:.1f} kWh/100 km** (estimated), at an average {d['avg_speed_kmh']:.0f} km/h: "
        "city driving, where the car is at its most frugal.",
        f"**{c['sessions']} charging sessions for {c['kwh']:.0f} kWh and €{c['cost_eur']:.2f}**"
        + (f", most of it at {loc['name']} ({loc['kwh']:.0f} kWh)." if loc else ".")
        + (f" Last week: {pc['sessions']} sessions, €{pc['cost_eur']:.2f}." if pc["cost_eur"] is not None else ""),
    ]
    if c.get("potential_saving_eur"):
        bullets.append(f"**Shifting peak-rate charging to the cheapest window would have saved about €{c['potential_saving_eur']:.2f}.** "
                       "Small money, but free to take.")
    if b["soc_min_pct"] is not None:
        bullets.append(f"The battery stayed between **{b['soc_min_pct']:.0f} % and {b['soc_max_pct']:.0f} %**, the range it is happiest in.")
    return (f"## {d['km']:.0f} km of calm commuting, charged cheaply\n\n" + "\n".join(f"- {x}" for x in bullets)
            + "\n\nWorth watching: nothing alarming. This is a sample report with made-up figures, written by hand for the demo.")


def add_reports(db, now):
    """Store a sample report for each of the last two full weeks (the Report tab's week picker)."""
    for back in (0, 7):
        wid = stats.last_full_week(now - dt.timedelta(days=back))
        w = stats.weekly(db, wid)
        if w["driving"]["trips"] and w["charging"]["sessions"] and w["charging"]["cost_eur"] is not None:
            report.store(db, wid, sample_report(w), w, "hand-written sample")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/demo.db"
    Path(path).unlink(missing_ok=True)
    db = connect(path)
    events = sorted(build(time.time() - 120), key=lambda e: e[0])
    for ts, domain, payload in events:
        store(db, VIN, domain, payload)
    db.commit()
    add_reports(db, dt.datetime.now(TZ))
    print(f"{len(events)} synthetic events written to {path}")


if __name__ == "__main__":
    main()
