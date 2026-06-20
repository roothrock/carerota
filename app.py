from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import sqlite3, os, smtplib, json
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "changeme123")

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")

DB = "rota.db"

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            off_days TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT,
            preferred_staff TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS rota (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            client_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            FOREIGN KEY(client_id) REFERENCES clients(id),
            FOREIGN KEY(staff_id) REFERENCES staff(id)
        );
        """)

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_week_start(offset=0):
    today = datetime.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    return monday.strftime("%Y-%m-%d")

def week_dates(week_start):
    start = datetime.strptime(week_start, "%Y-%m-%d")
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

# ── Routes: Staff ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("rota_view"))

@app.route("/staff")
def staff_list():
    with get_db() as db:
        staff = db.execute("SELECT * FROM staff ORDER BY name").fetchall()
    return render_template("staff.html", staff=staff, days=DAYS)

@app.route("/staff/add", methods=["POST"])
def staff_add():
    name     = request.form["name"].strip()
    email    = request.form.get("email", "").strip()
    off_days = request.form.getlist("off_days")
    with get_db() as db:
        db.execute("INSERT INTO staff (name, email, off_days) VALUES (?,?,?)",
                   (name, email, json.dumps(off_days)))
    flash(f"{name} added.")
    return redirect(url_for("staff_list"))

@app.route("/staff/<int:sid>/edit", methods=["GET", "POST"])
def staff_edit(sid):
    with get_db() as db:
        member = db.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone()
        if request.method == "POST":
            name     = request.form["name"].strip()
            email    = request.form.get("email", "").strip()
            off_days = request.form.getlist("off_days")
            db.execute("UPDATE staff SET name=?, email=?, off_days=? WHERE id=?",
                       (name, email, json.dumps(off_days), sid))
            flash(f"{name} updated.")
            return redirect(url_for("staff_list"))
    return render_template("staff_edit.html", member=member, days=DAYS,
                           off_days=json.loads(member["off_days"]))

@app.route("/staff/<int:sid>/delete", methods=["POST"])
def staff_delete(sid):
    with get_db() as db:
        db.execute("DELETE FROM staff WHERE id=?", (sid,))
    flash("Staff member removed.")
    return redirect(url_for("staff_list"))

# ── Routes: Clients ───────────────────────────────────────────────────────────

@app.route("/clients")
def client_list():
    with get_db() as db:
        clients = db.execute("SELECT * FROM clients ORDER BY name").fetchall()
        staff   = db.execute("SELECT * FROM staff ORDER BY name").fetchall()
    return render_template("clients.html", clients=clients, staff=staff)

@app.route("/clients/add", methods=["POST"])
def client_add():
    name      = request.form["name"].strip()
    address   = request.form.get("address", "").strip()
    preferred = request.form.getlist("preferred_staff")
    with get_db() as db:
        db.execute("INSERT INTO clients (name, address, preferred_staff) VALUES (?,?,?)",
                   (name, address, json.dumps(preferred)))
    flash(f"{name} added.")
    return redirect(url_for("client_list"))

@app.route("/clients/<int:cid>/edit", methods=["GET", "POST"])
def client_edit(cid):
    with get_db() as db:
        client = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        staff  = db.execute("SELECT * FROM staff ORDER BY name").fetchall()
        if request.method == "POST":
            name      = request.form["name"].strip()
            address   = request.form.get("address", "").strip()
            preferred = request.form.getlist("preferred_staff")
            db.execute("UPDATE clients SET name=?, address=?, preferred_staff=? WHERE id=?",
                       (name, address, json.dumps(preferred), cid))
            flash(f"{name} updated.")
            return redirect(url_for("client_list"))
    return render_template("client_edit.html", client=client, staff=staff,
                           preferred=json.loads(client["preferred_staff"]))

@app.route("/clients/<int:cid>/delete", methods=["POST"])
def client_delete(cid):
    with get_db() as db:
        db.execute("DELETE FROM clients WHERE id=?", (cid,))
    flash("Client removed.")
    return redirect(url_for("client_list"))

# ── Routes: Rota ──────────────────────────────────────────────────────────────

@app.route("/rota")
def rota_view():
    offset     = int(request.args.get("week", 0))
    week_start = get_week_start(offset)
    with get_db() as db:
        clients  = db.execute("SELECT * FROM clients ORDER BY name").fetchall()
        staff    = db.execute("SELECT * FROM staff ORDER BY name").fetchall()
        entries  = db.execute(
            "SELECT * FROM rota WHERE week_start=?", (week_start,)).fetchall()

    # Build lookup: {client_id: {day: staff_id}}
    rota_map = {}
    for e in entries:
        rota_map.setdefault(e["client_id"], {})[e["day"]] = e["staff_id"]

    # Staff off days lookup
    off_map = {s["id"]: json.loads(s["off_days"]) for s in staff}

    # Preferred staff lookup
    preferred_map = {c["id"]: json.loads(c["preferred_staff"]) for c in clients}

    # Per-day staff booking count (for double-booking detection)
    booked = {}
    for e in entries:
        booked.setdefault(e["day"], {}).setdefault(e["staff_id"], 0)
        booked[e["day"]][e["staff_id"]] += 1

    start_dt  = datetime.strptime(week_start, "%Y-%m-%d")
    week_label = f"{start_dt.strftime('%d %b')} – {(start_dt + timedelta(days=6)).strftime('%d %b %Y')}"

    return render_template("rota.html",
        clients=clients, staff=staff, days=DAYS,
        rota_map=rota_map, off_map=off_map, preferred_map=preferred_map,
        booked=booked, week_start=week_start, week_label=week_label,
        offset=offset)

@app.route("/rota/assign", methods=["POST"])
def rota_assign():
    week_start = request.form["week_start"]
    client_id  = int(request.form["client_id"])
    day        = request.form["day"]
    staff_id   = request.form.get("staff_id", "")
    offset     = request.form.get("offset", 0)

    with get_db() as db:
        db.execute("DELETE FROM rota WHERE week_start=? AND client_id=? AND day=?",
                   (week_start, client_id, day))
        if staff_id:
            db.execute("INSERT INTO rota (week_start, client_id, staff_id, day) VALUES (?,?,?,?)",
                       (week_start, client_id, int(staff_id), day))

    return redirect(url_for("rota_view", week=offset))

@app.route("/rota/send", methods=["POST"])
def rota_send():
    week_start = request.form["week_start"]
    offset     = request.form.get("offset", 0)

    with get_db() as db:
        staff   = db.execute("SELECT * FROM staff ORDER BY name").fetchall()
        clients = db.execute("SELECT * FROM clients ORDER BY name").fetchall()
        entries = db.execute(
            """SELECT r.*, s.name as sname, c.name as cname, c.address
               FROM rota r
               JOIN staff s ON r.staff_id = s.id
               JOIN clients c ON r.client_id = c.id
               WHERE r.week_start = ?""", (week_start,)).fetchall()

    # Group entries per staff member
    staff_shifts = {s["id"]: {"name": s["name"], "email": s["email"], "shifts": []}
                    for s in staff}
    for e in entries:
        staff_shifts[e["staff_id"]]["shifts"].append({
            "day": e["day"], "client": e["cname"], "address": e["address"]
        })

    start_dt   = datetime.strptime(week_start, "%Y-%m-%d")
    week_label = f"{start_dt.strftime('%d %b')} - {(start_dt + timedelta(days=6)).strftime('%d %b %Y')}"

    sent, skipped = 0, 0
    for sid, data in staff_shifts.items():
        if not data["email"] or not data["shifts"]:
            skipped += 1
            continue
        shifts_sorted = sorted(data["shifts"], key=lambda x: DAYS.index(x["day"]))
        lines = "\n".join(
            f"  {s['day']}: {s['client']} — {s['address']}" for s in shifts_sorted)
        body = (f"Hi {data['name']},\n\n"
                f"Your shifts for week {week_label}:\n\n{lines}\n\n"
                f"Please confirm receipt. Thank you.")
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Your rota — week of {week_label}"
            msg["From"]    = GMAIL_USER
            msg["To"]      = data["email"]
            msg.attach(MIMEText(body, "plain"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
                srv.login(GMAIL_USER, GMAIL_PASS)
                srv.sendmail(GMAIL_USER, data["email"], msg.as_string())
            sent += 1
        except Exception as ex:
            flash(f"Failed to email {data['name']}: {ex}")

    flash(f"Rota sent to {sent} staff member(s). {skipped} skipped (no email or no shifts).")
    return redirect(url_for("rota_view", week=offset))

if __name__ == "__main__":
    app.run(debug=True, port=5000)
