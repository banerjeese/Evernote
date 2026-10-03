import os, re, html
from flask import Flask, Response, jsonify, request, send_from_directory
from mysql.connector import pooling

DB = dict(
    host=os.getenv("DB_HOST", "100.125.233.115"),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", "dbuser"),
    password=os.getenv("DB_PASSWORD", "232527"),
    database=os.getenv("DB_NAME", "ProductivityAnalytics"),
    charset="utf8mb4",
)
USER_ID = 1  # single user, no auth

pool = pooling.MySQLConnectionPool(
    pool_name="notes", pool_size=8, pool_reset_session=False, autocommit=True, **DB
)
app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # max upload (images)


def snip(raw):
    t = (raw or "").replace("<!--rt-->", "")
    t = re.sub(r'<div class="i"[^>]*></div>', " 🖼 ", t)
    t = re.sub(r"<[^>]*$", "", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()[:140]


def run(sql, args=(), fetch="all"):
    """fetch: 'all' | 'one' | 'write' (returns (lastrowid, rowcount))."""
    conn = pool.get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, args)
        if fetch == "write":
            out = (cur.lastrowid, cur.rowcount)
        elif fetch == "one":
            out = cur.fetchone()
        else:
            out = cur.fetchall()
        cur.close()
        return out
    finally:
        conn.close()


D = "DATE_FORMAT({0},'%%Y-%%m-%%d')"
T = "TIME_FORMAT({0},'%%H:%%i')"


IMG_SQL = """CREATE TABLE IF NOT EXISTS note_images (
  image_id   INT AUTO_INCREMENT PRIMARY KEY,
  subnote_id INT NOT NULL,
  mime       VARCHAR(40) NOT NULL,
  data       MEDIUMBLOB NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_subnote (subnote_id)
) ENGINE=InnoDB"""
try:
    run(IMG_SQL, (), "write")
    IMG_OK = True
except Exception as e:  # e.g. dbuser has no CREATE permission
    IMG_OK = False
    print("!! Could not create table note_images:", e)
    print("!! Run this once in MySQL, then restart:\n" + IMG_SQL)


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


# ---------- notes ----------
@app.get("/api/notes")
def list_notes():
    return jsonify(run(
        f"""SELECT n.note_id id, n.note_topic topic, {D.format('n.note_date')} date,
                   {T.format('n.note_time')} time,
                   (SELECT COUNT(*) FROM subnotes s WHERE s.note_id = n.note_id) cnt
            FROM notes n WHERE n.userId=%s ORDER BY n.note_id DESC""", (USER_ID,)))


@app.post("/api/notes")
def add_note():
    topic = (request.json.get("topic") or "Untitled").strip()[:255]
    nid, _ = run("INSERT INTO notes (userId, note_topic, note_date, note_time) "
                 "VALUES (%s,%s,CURDATE(),CURTIME())", (USER_ID, topic), "write")
    return jsonify(run(
        f"""SELECT note_id id, note_topic topic, {D.format('note_date')} date,
                   {T.format('note_time')} time, 0 cnt FROM notes WHERE note_id=%s""",
        (nid,), "one")), 201


@app.patch("/api/notes/<int:nid>")
def rename_note(nid):
    topic = (request.json.get("topic") or "Untitled").strip()[:255]
    run("UPDATE notes SET note_topic=%s WHERE note_id=%s AND userId=%s",
        (topic, nid, USER_ID), "write")
    return jsonify(ok=True)


@app.delete("/api/notes/<int:nid>")
def del_note(nid):
    if IMG_OK:
        run("DELETE i FROM note_images i JOIN subnotes s ON s.subnote_id=i.subnote_id "
            "JOIN notes n ON n.note_id=s.note_id WHERE s.note_id=%s AND n.userId=%s",
            (nid, USER_ID), "write")
    run("DELETE s FROM subnotes s JOIN notes n ON n.note_id=s.note_id "
        "WHERE s.note_id=%s AND n.userId=%s", (nid, USER_ID), "write")
    run("DELETE FROM notes WHERE note_id=%s AND userId=%s", (nid, USER_ID), "write")
    return jsonify(ok=True)


# ---------- subnotes ----------
@app.get("/api/notes/<int:nid>/subnotes")
def list_subs(nid):
    # snippet only: full description is fetched when a subnote is opened
    rows = run(
        f"""SELECT s.subnote_id id, s.subnote_topic topic,
                   LEFT(IFNULL(s.description,''),800) snippet,
                   {D.format('s.subnote_date')} date, {T.format('s.subnote_time')} time
            FROM subnotes s JOIN notes n ON n.note_id=s.note_id
            WHERE s.note_id=%s AND n.userId=%s ORDER BY s.subnote_id DESC""",
        (nid, USER_ID))
    for r in rows:
        r["snippet"] = snip(r["snippet"])
    return jsonify(rows)


@app.post("/api/notes/<int:nid>/subnotes")
def add_sub(nid):
    j = request.json or {}
    topic = (j.get("topic") or "Untitled").strip()[:255]
    sid, _ = run(
        "INSERT INTO subnotes (note_id, subnote_topic, description, subnote_date, subnote_time) "
        "SELECT note_id,%s,%s,CURDATE(),CURTIME() FROM notes WHERE note_id=%s AND userId=%s",
        (topic, j.get("description", ""), nid, USER_ID), "write")
    return jsonify(run(
        f"""SELECT subnote_id id, subnote_topic topic, '' snippet,
                   {D.format('subnote_date')} date, {T.format('subnote_time')} time
            FROM subnotes WHERE subnote_id=%s""", (sid,), "one")), 201


@app.get("/api/subnotes/<int:sid>")
def get_sub(sid):
    row = run("SELECT s.subnote_id id, s.subnote_topic topic, IFNULL(s.description,'') description "
              "FROM subnotes s JOIN notes n ON n.note_id=s.note_id "
              "WHERE s.subnote_id=%s AND n.userId=%s", (sid, USER_ID), "one")
    return (jsonify(row), 200) if row else (jsonify(error="not found"), 404)


@app.patch("/api/subnotes/<int:sid>")
def edit_sub(sid):
    j = request.json or {}
    sets, args = [], []
    if "topic" in j:
        sets.append("s.subnote_topic=%s"); args.append((j["topic"] or "Untitled").strip()[:255])
    if "description" in j:
        sets.append("s.description=%s"); args.append(j["description"])
    if sets:
        run(f"UPDATE subnotes s JOIN notes n ON n.note_id=s.note_id SET {','.join(sets)} "
            "WHERE s.subnote_id=%s AND n.userId=%s", (*args, sid, USER_ID), "write")
    return jsonify(ok=True)


@app.delete("/api/subnotes/<int:sid>")
def del_sub(sid):
    if IMG_OK:
        run("DELETE i FROM note_images i JOIN subnotes s ON s.subnote_id=i.subnote_id "
            "JOIN notes n ON n.note_id=s.note_id WHERE i.subnote_id=%s AND n.userId=%s",
            (sid, USER_ID), "write")
    run("DELETE s FROM subnotes s JOIN notes n ON n.note_id=s.note_id "
        "WHERE s.subnote_id=%s AND n.userId=%s", (sid, USER_ID), "write")
    return jsonify(ok=True)


# ---------- images (stored in MySQL, served with long cache) ----------
ALLOWED = {"image/png", "image/jpeg", "image/gif", "image/webp"}


@app.post("/api/subnotes/<int:sid>/images")
def add_image(sid):
    if not IMG_OK:
        return jsonify(error="note_images table is missing"), 503
    mime, data = (request.mimetype or "").lower(), request.get_data()
    if mime not in ALLOWED or not data:
        return jsonify(error="unsupported image"), 400
    iid, n = run("INSERT INTO note_images (subnote_id, mime, data) "
                 "SELECT s.subnote_id,%s,%s FROM subnotes s JOIN notes n ON n.note_id=s.note_id "
                 "WHERE s.subnote_id=%s AND n.userId=%s", (mime, data, sid, USER_ID), "write")
    return (jsonify(id=iid), 201) if n else (jsonify(error="not found"), 404)


@app.get("/api/images/<int:iid>")
def get_image(iid):
    row = IMG_OK and run(
        "SELECT i.mime, i.data FROM note_images i JOIN subnotes s ON s.subnote_id=i.subnote_id "
        "JOIN notes n ON n.note_id=s.note_id WHERE i.image_id=%s AND n.userId=%s",
        (iid, USER_ID), "one")
    if not row:
        return "", 404
    r = Response(bytes(row["data"]), mimetype=row["mime"])
    r.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return r


@app.delete("/api/images/<int:iid>")
def del_image(iid):
    if IMG_OK:
        run("DELETE i FROM note_images i JOIN subnotes s ON s.subnote_id=i.subnote_id "
            "JOIN notes n ON n.note_id=s.note_id WHERE i.image_id=%s AND n.userId=%s",
            (iid, USER_ID), "write")
    return jsonify(ok=True)


if __name__ == "__main__":
    from waitress import serve
    print("Running on http://localhost:4500")
    serve(app, host="0.0.0.0", port=4500, threads=8)