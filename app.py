import os
from flask import Flask, jsonify, request, send_from_directory
from mysql.connector import pooling

DB = dict(
    host=os.getenv("DB_HOST", "100.125.233.115"),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", "dbuser"),
    password=os.getenv("DB_PASSWORD", "232527"),
    database=os.getenv("DB_NAME", "ProductivityAnalytics"),
)
USER_ID = 1  # single user, no auth

pool = pooling.MySQLConnectionPool(
    pool_name="notes", pool_size=8, pool_reset_session=False, autocommit=True, **DB
)
app = Flask(__name__, static_folder="static", static_url_path="")


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
    run("DELETE s FROM subnotes s JOIN notes n ON n.note_id=s.note_id "
        "WHERE s.note_id=%s AND n.userId=%s", (nid, USER_ID), "write")
    run("DELETE FROM notes WHERE note_id=%s AND userId=%s", (nid, USER_ID), "write")
    return jsonify(ok=True)


# ---------- subnotes ----------
@app.get("/api/notes/<int:nid>/subnotes")
def list_subs(nid):
    # snippet only: full description is fetched when a subnote is opened
    return jsonify(run(
        f"""SELECT s.subnote_id id, s.subnote_topic topic,
                   LEFT(IFNULL(s.description,''),140) snippet,
                   {D.format('s.subnote_date')} date, {T.format('s.subnote_time')} time
            FROM subnotes s JOIN notes n ON n.note_id=s.note_id
            WHERE s.note_id=%s AND n.userId=%s ORDER BY s.subnote_id DESC""",
        (nid, USER_ID)))


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
    run("DELETE s FROM subnotes s JOIN notes n ON n.note_id=s.note_id "
        "WHERE s.subnote_id=%s AND n.userId=%s", (sid, USER_ID), "write")
    return jsonify(ok=True)


if __name__ == "__main__":
    from waitress import serve
    print("Running on http://localhost:4500")
    serve(app, host="0.0.0.0", port=4500, threads=8)