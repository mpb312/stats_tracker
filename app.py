from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta
import os

DB_PATH = Path(os.environ.get("DB_PATH", "stats.db"))

app = FastAPI()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    """
    ddl example: "value_hits INTEGER"
    """
    cols = _table_columns(conn, table)
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        conn.commit()


def init_db():
    conn = db()
    cur = conn.cursor()

    # New-ish schema for stats
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'numeric',
            created_at TEXT NOT NULL
        )
    """)

    # Entries table supports numeric, boolean_daily, ratio
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_id INTEGER NOT NULL,
            day TEXT NOT NULL,                 -- YYYY-MM-DD (the day the entry is FOR)
            value_num REAL,                    -- numeric stats
            value_bool INTEGER,                -- 0/1 for boolean_daily stats
            value_hits INTEGER,                -- ratio stats (hits)
            value_total INTEGER,               -- ratio stats (total)
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(stat_id) REFERENCES stats(id) ON DELETE CASCADE,
            UNIQUE(stat_id, day)
        )
    """)
    conn.commit()

    # Ensure columns exist if table pre-existed in a partially migrated state
    try:
        add_column_if_missing(conn, "stats", "unit", "unit TEXT NOT NULL DEFAULT ''")
        add_column_if_missing(conn, "stats", "kind", "kind TEXT NOT NULL DEFAULT 'numeric'")

        add_column_if_missing(conn, "entries", "day", "day TEXT")
        add_column_if_missing(conn, "entries", "value_num", "value_num REAL")
        add_column_if_missing(conn, "entries", "value_bool", "value_bool INTEGER")
        add_column_if_missing(conn, "entries", "value_hits", "value_hits INTEGER")
        add_column_if_missing(conn, "entries", "value_total", "value_total INTEGER")
        add_column_if_missing(conn, "entries", "note", "note TEXT")
        add_column_if_missing(conn, "entries", "created_at", "created_at TEXT")
    except Exception:
        pass

    # Best-effort migration from the very first version (entries had "value" column, no day)
    # If we detect old entries schema, rebuild and copy.
    try:
        entries_cols = _table_columns(conn, "entries")
        if "value" in entries_cols:
            # Old schema detected: rename old table, create new, copy value -> value_num
            conn.execute("ALTER TABLE entries RENAME TO entries_old")

            conn.execute("""
                CREATE TABLE entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stat_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    value_num REAL,
                    value_bool INTEGER,
                    value_hits INTEGER,
                    value_total INTEGER,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(stat_id) REFERENCES stats(id) ON DELETE CASCADE,
                    UNIQUE(stat_id, day)
                )
            """)

            old_rows = conn.execute("SELECT * FROM entries_old ORDER BY id ASC").fetchall()
            for r in old_rows:
                created = r["created_at"] or datetime.utcnow().isoformat()
                derived_day = created.split("T")[0] if "T" in created else created[:10]
                conn.execute(
                    "INSERT OR REPLACE INTO entries (stat_id, day, value_num, value_bool, value_hits, value_total, note, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["stat_id"], derived_day, r["value"], None, None, None, r["note"], created),
                )

            conn.execute("DROP TABLE entries_old")
            conn.commit()
    except Exception:
        pass

    conn.close()


init_db()


def layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      margin: 0;
      background: #fafafa;
      color: #111;
    }}
    .wrap {{
      max-width: 420px;
      margin: 0 auto;
      padding: 16px;
    }}
    h1 {{ font-size: 22px; margin: 8px 0 4px; }}
    p {{ color: #555; margin-top: 0; }}

    .card {{
      background: #fff;
      border: 1px solid #e5e5e5;
      border-radius: 16px;
      padding: 14px;
      box-shadow: 0 1px 2px rgba(0,0,0,.04);
      margin: 12px 0;
    }}

    input, button, textarea, select {{
      width: 100%;
      box-sizing: border-box;
      font-size: 16px;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid #ddd;
      margin-top: 6px;
      background: #fff;
    }}

    button {{
      background: #111;
      color: white;
      border: none;
      font-weight: 600;
      margin-top: 10px;
    }}

    .btn-secondary {{
      background: #f4f4f5;
      color: #111;
      border: 1px solid #e5e5e5;
    }}

    .btn-danger {{
      background: #ef4444;
      color: white;
    }}

    a {{ color: inherit; text-decoration: none; }}
    .muted {{ color: #666; font-size: 13px; }}

    .row {{
      display: flex;
      gap: 10px;
    }}
    .row > * {{
      flex: 1;
    }}

    /* Stats list */
    .stat-row {{
      display: flex;
      gap: 10px;
      align-items: stretch;
      margin-top: 10px;
    }}
    .stat-link {{
      flex: 1;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px;
      border-radius: 14px;
      border: 1px solid #eee;
      background: #fff;
    }}
    .stat-actions {{
      width: 92px;
      display: flex;
    }}
    .stat-actions form {{
      width: 100%;
      margin: 0;
    }}
    .stat-actions button {{
      margin-top: 0;
      width: 100%;
      padding: 10px 10px;
      border-radius: 14px;
      font-size: 14px;
    }}

    /* Numeric + ratio entries list */
    .entry {{
      border-top: 1px solid #eee;
      padding: 10px 0;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: start;
    }}
    .entry:first-child {{ border-top: none; }}
    .entry-main {{ flex: 1; }}
    .entry-actions form {{ margin: 0; }}
    .entry-actions button {{
      margin-top: 0;
      padding: 8px 10px;
      border-radius: 12px;
      font-size: 13px;
    }}

    /* Boolean daily */
    .day-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid #eee;
      margin-top: 10px;
      background: #fff;
    }}
    .pill {{
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 13px;
      border: 1px solid #e5e5e5;
      background: #f4f4f5;
      display: inline-block;
      margin-top: 6px;
    }}
    .pill-yes {{
      background: #dcfce7;
      border-color: #86efac;
    }}
    .pill-no {{
      background: #fee2e2;
      border-color: #fca5a5;
    }}
    .day-actions form {{ margin: 0; }}
    .day-actions button {{
      margin-top: 0;
      width: auto;
      padding: 8px 12px;
      border-radius: 12px;
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    {body}
  </div>
</body>
</html>
"""


def kind_label(stat_kind: str) -> str:
    if stat_kind == "numeric":
        return "Numeric"
    if stat_kind == "boolean_daily":
        return "Daily yes/no"
    if stat_kind == "ratio":
        return "Hits / Total"
    return "Numeric"


@app.get("/", response_class=HTMLResponse)
def home():
    conn = db()
    stats = conn.execute("SELECT * FROM stats ORDER BY id DESC").fetchall()
    conn.close()

    stats_html = ""
    if not stats:
        stats_html = '<div class="muted">No stats yet — add “Weight (kg)”, “Worked out?”, or “Bullseyes”.</div>'
    else:
        for s in stats:
            k = s["kind"]
            unit_part = f" • Unit: {s['unit']}" if k == "numeric" and s["unit"] else ""
            stats_html += f"""
              <div class="stat-row">
                <a class="stat-link" href="/stat/{s['id']}">
                  <div>
                    <div><b>{s['name']}</b></div>
                    <div class="muted">{kind_label(k)}{unit_part}</div>
                  </div>
                  <div class="muted">›</div>
                </a>

                <div class="stat-actions">
                  <form method="post" action="/stat/{s['id']}/delete">
                    <button class="btn-danger" type="submit">Delete</button>
                  </form>
                </div>
              </div>
            """

    body = f"""
      <h1>Stat Tracker</h1>
      <p>Create a statistic, then tap it to add entries or toggle days.</p>

      <div class="card">
        <b>Create new statistic</b>
        <form method="post" action="/stats">
          <label class="muted">Type</label>
          <select name="kind">
            <option value="numeric" selected>Numeric (e.g. Weight)</option>
            <option value="boolean_daily">Daily yes/no (e.g. Worked out?)</option>
            <option value="ratio">Hits / Total (e.g. Bullseyes)</option>
          </select>

          <label class="muted">Name</label>
          <input name="name" placeholder="Weight / Worked out? / Bullseyes" required />

          <label class="muted">Unit (numeric only)</label>
          <input name="unit" placeholder="kg" />

          <button type="submit">Add statistic</button>
        </form>
      </div>

      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:baseline;">
          <b>Your statistics</b>
          <span class="muted">{len(stats)}</span>
        </div>
        {stats_html}
      </div>
    """
    return layout("Stat Tracker", body)


@app.post("/stats")
def create_stat(name: str = Form(...), kind: str = Form("numeric"), unit: str = Form("")):
    kind = (kind or "numeric").strip()
    if kind not in ("numeric", "boolean_daily", "ratio"):
        kind = "numeric"

    unit_clean = unit.strip()
    if kind != "numeric":
        unit_clean = ""

    conn = db()
    conn.execute(
        "INSERT INTO stats (name, unit, kind, created_at) VALUES (?, ?, ?, ?)",
        (name.strip(), unit_clean, kind, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)


@app.get("/stat/{stat_id}", response_class=HTMLResponse)
def stat_detail(stat_id: int):
    conn = db()
    stat = conn.execute("SELECT * FROM stats WHERE id = ?", (stat_id,)).fetchone()
    if not stat:
        conn.close()
        return HTMLResponse("Not found", status_code=404)

    kind = stat["kind"]

    # ---- Boolean daily UI ----
    if kind == "boolean_daily":
        today = date.today()
        days = [(today - timedelta(days=i)) for i in range(14)]
        day_strs = [d.isoformat() for d in days]

        rows = conn.execute(
            f"SELECT day, value_bool FROM entries WHERE stat_id = ? AND day IN ({','.join(['?']*len(day_strs))})",
            (stat_id, *day_strs),
        ).fetchall()
        conn.close()

        true_days = {r["day"] for r in rows if r["value_bool"] == 1}

        day_rows_html = ""
        for d in days:
            ds = d.isoformat()
            is_true = ds in true_days
            label = "Yes ✅" if is_true else "No ⬜"
            pill_class = "pill pill-yes" if is_true else "pill pill-no"
            toggle_to = "0" if is_true else "1"
            btn_text = "Set No" if is_true else "Set Yes"

            if ds == today.isoformat():
                nice = "Today"
            elif ds == (today - timedelta(days=1)).isoformat():
                nice = "Yesterday"
            else:
                nice = ds

            day_rows_html += f"""
              <div class="day-row">
                <div>
                  <div><b>{nice}</b></div>
                  <div class="{pill_class}">{label}</div>
                </div>
                <div class="day-actions">
                  <form method="post" action="/stat/{stat_id}/bool">
                    <input type="hidden" name="day" value="{ds}" />
                    <input type="hidden" name="value_bool" value="{toggle_to}" />
                    <button class="btn-secondary" type="submit">{btn_text}</button>
                  </form>
                </div>
              </div>
            """

        body = f"""
          <div style="margin-bottom:10px;">
            <a class="muted" href="/">← Back</a>
          </div>

          <h1>{stat['name']}</h1>
          <p class="muted">Type: Daily yes/no (default is No unless you set Yes). Tap any day to edit.</p>

          <div class="card">
            <b>Last 14 days</b>
            {day_rows_html}
          </div>
        """
        return layout(f"{stat['name']} — Stat Tracker", body)

    # ---- Ratio UI (hits / total) ----
    if kind == "ratio":
        entries = conn.execute(
            "SELECT * FROM entries WHERE stat_id = ? ORDER BY day DESC LIMIT 50",
            (stat_id,),
        ).fetchall()
        conn.close()

        entries_html = ""
        if not entries:
            entries_html = '<div class="muted">No entries yet.</div>'
        else:
            for e in entries:
                hits = e["value_hits"] if e["value_hits"] is not None else 0
                total = e["value_total"] if e["value_total"] is not None else 0
                pct = (hits / total * 100.0) if total else 0.0
                note = f"<div class='muted'>{e['note']}</div>" if e["note"] else ""
                entries_html += f"""
                  <div class="entry">
                    <div class="entry-main">
                      <div><b>{hits}/{total}</b> <span class="muted">({pct:.1f}%) • {e['day']}</span></div>
                      {note}
                    </div>
                    <div class="entry-actions">
                      <form method="post" action="/entry/{e['id']}/delete">
                        <input type="hidden" name="stat_id" value="{stat_id}" />
                        <button class="btn-secondary" type="submit">Remove</button>
                      </form>
                    </div>
                  </div>
                """

        body = f"""
          <div style="margin-bottom:10px;">
            <a class="muted" href="/">← Back</a>
          </div>

          <h1>{stat['name']}</h1>
          <p class="muted">Type: Hits / Total • Example: bullseyes hit out of darts thrown</p>

          <div class="card">
            <b>Add entry</b>
            <form method="post" action="/stat/{stat_id}/ratio">
              <div class="row">
                <div>
                  <label class="muted">Hits</label>
                  <input name="hits" inputmode="numeric" placeholder="7" required />
                </div>
                <div>
                  <label class="muted">Total</label>
                  <input name="total" inputmode="numeric" placeholder="50" required />
                </div>
              </div>

              <label class="muted">Day (YYYY-MM-DD)</label>
              <input name="day" placeholder="{date.today().isoformat()}" />

              <label class="muted">Note (optional)</label>
              <textarea name="note" rows="2" placeholder="Practice session details..."></textarea>

              <button type="submit">Save entry</button>
            </form>
          </div>

          <div class="card">
            <b>Recent entries</b>
            {entries_html}
          </div>
        """
        return layout(f"{stat['name']} — Stat Tracker", body)

    # ---- Numeric UI (default) ----
    entries = conn.execute(
        "SELECT * FROM entries WHERE stat_id = ? ORDER BY day DESC LIMIT 50",
        (stat_id,),
    ).fetchall()
    conn.close()

    entries_html = ""
    if not entries:
        entries_html = '<div class="muted">No entries yet.</div>'
    else:
        for e in entries:
            note = f"<div class='muted'>{e['note']}</div>" if e["note"] else ""
            entries_html += f"""
              <div class="entry">
                <div class="entry-main">
                  <div><b>{e['value_num']}</b> {stat['unit']} <span class="muted">({e['day']})</span></div>
                  {note}
                </div>
                <div class="entry-actions">
                  <form method="post" action="/entry/{e['id']}/delete">
                    <input type="hidden" name="stat_id" value="{stat_id}" />
                    <button class="btn-secondary" type="submit">Remove</button>
                  </form>
                </div>
              </div>
            """

    body = f"""
      <div style="margin-bottom:10px;">
        <a class="muted" href="/">← Back</a>
      </div>

      <h1>{stat['name']}</h1>
      <p class="muted">Type: Numeric • Unit: {stat['unit']}</p>

      <div class="card">
        <b>Add entry</b>
        <form method="post" action="/stat/{stat_id}/entries">
          <label class="muted">Value</label>
          <input name="value" inputmode="decimal" placeholder="82.4" required />

          <label class="muted">Day (YYYY-MM-DD)</label>
          <input name="day" placeholder="{date.today().isoformat()}" />

          <label class="muted">Note (optional)</label>
          <textarea name="note" rows="2" placeholder="Fasted, morning weigh-in"></textarea>

          <button type="submit">Save entry</button>
        </form>
      </div>

      <div class="card">
        <b>Recent entries</b>
        {entries_html}
      </div>
    """
    return layout(f"{stat['name']} — Stat Tracker", body)


@app.post("/stat/{stat_id}/entries")
def add_numeric_entry(stat_id: int, value: str = Form(...), day: str = Form(""), note: str = Form("")):
    d = (day or "").strip() or date.today().isoformat()
    v = float(value.replace(",", "."))

    conn = db()
    stat = conn.execute("SELECT kind FROM stats WHERE id = ?", (stat_id,)).fetchone()
    if not stat:
        conn.close()
        return RedirectResponse("/", status_code=303)

    if stat["kind"] != "numeric":
        conn.close()
        return RedirectResponse(f"/stat/{stat_id}", status_code=303)

    conn.execute(
        "INSERT OR REPLACE INTO entries (stat_id, day, value_num, value_bool, value_hits, value_total, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (stat_id, d, v, None, None, None, note.strip() or None, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/stat/{stat_id}", status_code=303)


@app.post("/stat/{stat_id}/ratio")
def add_ratio_entry(
    stat_id: int,
    hits: str = Form(...),
    total: str = Form(...),
    day: str = Form(""),
    note: str = Form(""),
):
    d = (day or "").strip() or date.today().isoformat()

    # Parse ints safely
    h = int(hits.strip())
    t = int(total.strip())

    # Basic validation
    if t <= 0:
        return RedirectResponse(f"/stat/{stat_id}", status_code=303)
    if h < 0:
        return RedirectResponse(f"/stat/{stat_id}", status_code=303)
    if h > t:
        return RedirectResponse(f"/stat/{stat_id}", status_code=303)

    conn = db()
    stat = conn.execute("SELECT kind FROM stats WHERE id = ?", (stat_id,)).fetchone()
    if not stat:
        conn.close()
        return RedirectResponse("/", status_code=303)

    if stat["kind"] != "ratio":
        conn.close()
        return RedirectResponse(f"/stat/{stat_id}", status_code=303)

    conn.execute(
        "INSERT OR REPLACE INTO entries (stat_id, day, value_num, value_bool, value_hits, value_total, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (stat_id, d, None, None, h, t, note.strip() or None, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/stat/{stat_id}", status_code=303)


@app.post("/stat/{stat_id}/bool")
def set_bool_day(stat_id: int, day: str = Form(...), value_bool: str = Form(...)):
    d = day.strip()
    vb = 1 if value_bool.strip() == "1" else 0

    conn = db()
    stat = conn.execute("SELECT kind FROM stats WHERE id = ?", (stat_id,)).fetchone()
    if not stat:
        conn.close()
        return RedirectResponse("/", status_code=303)

    if stat["kind"] != "boolean_daily":
        conn.close()
        return RedirectResponse(f"/stat/{stat_id}", status_code=303)

    if vb == 1:
        # Store explicit "true"
        conn.execute(
            "INSERT OR REPLACE INTO entries (stat_id, day, value_num, value_bool, value_hits, value_total, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (stat_id, d, None, 1, None, None, None, datetime.utcnow().isoformat()),
        )
    else:
        # Default false => delete row if it exists
        conn.execute("DELETE FROM entries WHERE stat_id = ? AND day = ?", (stat_id, d))

    conn.commit()
    conn.close()
    return RedirectResponse(f"/stat/{stat_id}", status_code=303)


@app.post("/stat/{stat_id}/delete")
def delete_stat(stat_id: int):
    conn = db()
    conn.execute("DELETE FROM stats WHERE id = ?", (stat_id,))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/entry/{entry_id}/delete")
def delete_entry(entry_id: int, stat_id: int = Form(...)):
    conn = db()
    conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/stat/{stat_id}", status_code=303)
