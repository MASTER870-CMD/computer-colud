# app.py - Mini Cloud Computer (Flask backend, production-ready for local use)
import os
import sqlite3
import uuid
import zipfile
import shutil
import platform
from datetime import datetime
from flask import Flask, request, jsonify, send_file, send_from_directory, abort
from werkzeug.utils import secure_filename
from flask import Flask, send_from_directory
import os

app = Flask(__name__, static_folder='frontend', static_url_path='')

@app.route('/')
def serve_index():
    return send_from_directory('frontend', 'index.html')




# Optional: psutil for richer system info (pip install psutil)
try:
    import psutil
except Exception:
    psutil = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
DB_PATH = os.path.join(BASE_DIR, "files.db")
ALLOWED_EXT = None  # None => allow all file types

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(FRONTEND_DIR, exist_ok=True)

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/")

# ---------- DB helpers ----------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS folders (
        id TEXT PRIMARY KEY, name TEXT, path TEXT, created TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS files (
        id TEXT PRIMARY KEY, filename TEXT, folder_id TEXT, path TEXT,
        mimetype TEXT, size INTEGER, trashed INTEGER DEFAULT 0, created TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, details TEXT, ts TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT)""")
    conn.commit(); conn.close()

def log(action, details=""):
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO logs (action,details,ts) VALUES (?,?,?)", (action, details, datetime.utcnow().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        pass

init_db()

# Ensure root folder exists
def ensure_root_folder():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM folders WHERE name = ?", ("root",))
    row = cur.fetchone()
    if not row:
        fid = str(uuid.uuid4()); folder_path = os.path.join(DATA_DIR, fid)
        os.makedirs(folder_path, exist_ok=True)
        cur.execute("INSERT INTO folders (id,name,path,created) VALUES (?,?,?,?)", (fid, "root", folder_path, datetime.utcnow().isoformat()))
        conn.commit()
    conn.close()

ensure_root_folder()

@app.route('/favicon.ico')
def favicon():
    p = os.path.join(FRONTEND_DIR, 'favicon.ico')
    if os.path.exists(p):
        return send_file(p)
    return ('', 204)

# -------- Folders --------
@app.route("/api/folders", methods=["GET","POST"])
def api_folders():
    conn = get_conn(); cur = conn.cursor()
    if request.method == "GET":
        cur.execute("SELECT id,name,created FROM folders ORDER BY name")
        rows = [dict(r) for r in cur.fetchall()]; conn.close(); return jsonify(rows)
    else:
        data = request.json or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error":"name required"}), 400
        fid = str(uuid.uuid4()); folder_path = os.path.join(DATA_DIR, fid)
        os.makedirs(folder_path, exist_ok=True)
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO folders (id,name,path,created) VALUES (?,?,?,?)", (fid, name, folder_path, datetime.utcnow().isoformat()))
        conn.commit(); conn.close(); log("create_folder", name)
        # return created folder id + name
        return jsonify({"id":fid,"name":name})

@app.route("/api/folders/<folder_id>", methods=["GET"])
def api_folder_info(folder_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id,name,created FROM folders WHERE id = ?", (folder_id,))
    row = cur.fetchone(); conn.close()
    if not row: return jsonify({"error":"not found"}), 404
    return jsonify(dict(row))

@app.route("/api/folders/<folder_id>/delete", methods=["POST","DELETE"])
def api_folder_delete(folder_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id,name,path FROM folders WHERE id = ?", (folder_id,))
    row = cur.fetchone()
    if not row:
        conn.close(); return jsonify({"error":"not found"}), 404
    if row["name"] == "root":
        conn.close(); return jsonify({"error":"cannot_delete_root"}), 400
    cur.execute("SELECT COUNT(*) as c FROM files WHERE folder_id = ?", (folder_id,))
    c = cur.fetchone()["c"]
    if c and c > 0:
        conn.close(); return jsonify({"error":"folder_not_empty","count":c}), 400
    # safe to remove folder
    try:
        if os.path.isdir(row["path"]):
            shutil.rmtree(row["path"])
    except Exception:
        pass
    cur.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    conn.commit(); conn.close(); log("delete_folder", folder_id)
    return jsonify({"ok":True})

# -------- Files (list/upload/create) --------
@app.route("/api/files", methods=["GET","POST"])
def api_files():
    conn = get_conn(); cur = conn.cursor()
    if request.method == "GET":
        # folder, q (search), trashed
        folder = request.args.get("folder", "")
        q = (request.args.get("q") or "").strip()
        trashed = request.args.get("trashed")
        # search both filename and folder name
        sql = """SELECT f.id,f.filename,f.mimetype,f.size,f.trashed,f.created,fo.name as folder_name
                 FROM files f LEFT JOIN folders fo ON f.folder_id = fo.id"""
        parts=[]; args=[]
        if folder:
            parts.append("f.folder_id = ?"); args.append(folder)
        if trashed in ("0","1"):
            parts.append("f.trashed = ?"); args.append(int(trashed))
        if q:
            parts.append("(LOWER(f.filename) LIKE ? OR LOWER(fo.name) LIKE ?)")
            qlike = f"%{q.lower()}%"
            args.extend([qlike, qlike])
        if parts:
            sql += " WHERE " + " AND ".join(parts)
        sql += " ORDER BY f.created DESC"
        cur.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]; conn.close(); return jsonify(rows)
    else:
        folder_id = request.form.get("folder")
        if not folder_id:
            return jsonify({"error":"folder id required"}), 400
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT path FROM folders WHERE id = ?", (folder_id,))
        row = cur.fetchone()
        if not row:
            conn.close(); return jsonify({"error":"folder not found"}), 404
        folder_path = row["path"]
        f = request.files.get("file")
        if not f:
            conn.close(); return jsonify({"error":"no file"}), 400
        filename = secure_filename(f.filename)
        if ALLOWED_EXT:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXT:
                return jsonify({"error":"file type not allowed"}), 400
        file_id = str(uuid.uuid4()); dest = os.path.join(folder_path, file_id + "_" + filename)
        f.save(dest)
        size = os.path.getsize(dest); mimetype = f.mimetype or "application/octet-stream"
        cur.execute("INSERT INTO files (id,filename,folder_id,path,mimetype,size,created) VALUES (?,?,?,?,?,?,?)",
                    (file_id, filename, folder_id, dest, mimetype, size, datetime.utcnow().isoformat()))
        conn.commit(); conn.close(); log("upload", filename); return jsonify({"id":file_id,"filename":filename})

@app.route("/api/files/create-empty", methods=["POST"])
def api_create_empty_file():
    folder_id = request.form.get("folder")
    filename = (request.form.get("filename") or "").strip()
    content = request.form.get("content", "")
    if not folder_id or not filename:
        return jsonify({"error":"folder and filename required"}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT path FROM folders WHERE id = ?", (folder_id,))
    row = cur.fetchone()
    if not row:
        conn.close(); return jsonify({"error":"folder not found"}), 404
    folder_path = row["path"]
    safe_name = secure_filename(filename)
    file_id = str(uuid.uuid4())
    dest = os.path.join(folder_path, file_id + "_" + safe_name)
    try:
        with open(dest, "wb") as fh:
            fh.write(content.encode("utf-8") if isinstance(content, str) else content)
    except Exception as e:
        conn.close(); return jsonify({"error":"write_failed","detail":str(e)}), 500
    size = os.path.getsize(dest)
    mimetype = "text/plain"
    cur.execute("INSERT INTO files (id,filename,folder_id,path,mimetype,size,created) VALUES (?,?,?,?,?,?,?)",
                (file_id, safe_name, folder_id, dest, mimetype, size, datetime.utcnow().isoformat()))
    conn.commit(); conn.close(); log("create_empty_file", safe_name)
    return jsonify({"id":file_id,"filename":safe_name})

# -------- Download / preview --------
@app.route("/api/files/<file_id>/download", methods=["GET"])
def api_download(file_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT path,filename FROM files WHERE id = ?", (file_id,))
    row = cur.fetchone(); conn.close()
    if not row: return abort(404)
    try:
        return send_file(row["path"], as_attachment=False, download_name=row["filename"])
    except Exception:
        return abort(404)

# -------- Trash / Restore / Delete --------
@app.route("/api/files/<file_id>/trash", methods=["POST"])
def api_trash(file_id):
    conn = get_conn(); cur = conn.cursor(); cur.execute("UPDATE files SET trashed = 1 WHERE id = ?", (file_id,)); conn.commit(); conn.close(); log("trash", file_id); return jsonify({"ok":True})

@app.route("/api/files/<file_id>/restore", methods=["POST"])
def api_restore(file_id):
    conn = get_conn(); cur = conn.cursor(); cur.execute("UPDATE files SET trashed = 0 WHERE id = ?", (file_id,)); conn.commit(); conn.close(); log("restore", file_id); return jsonify({"ok":True})

@app.route("/api/files/<file_id>/delete", methods=["DELETE"])
def api_delete_perm(file_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT path,filename FROM files WHERE id = ?", (file_id,))
    row = cur.fetchone()
    if row:
        try: os.remove(row["path"])
        except Exception: pass
    cur.execute("DELETE FROM files WHERE id = ?", (file_id,))
    conn.commit(); conn.close(); log("delete_perm", file_id); return jsonify({"ok":True})

# -------- Rename / Move --------
@app.route("/api/files/<file_id>/rename", methods=["POST"])
def api_rename_file(file_id):
    data = request.json or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error":"name required"}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT path,filename,folder_id FROM files WHERE id = ?", (file_id,))
    row = cur.fetchone()
    if not row:
        conn.close(); return jsonify({"error":"not found"}), 404
    old_path = row["path"]; safe_name = secure_filename(new_name)
    new_path = os.path.join(os.path.dirname(old_path), file_id + "_" + safe_name)
    try:
        os.rename(old_path, new_path)
    except Exception as e:
        conn.close(); return jsonify({"error":"rename_failed","detail":str(e)}), 500
    cur.execute("UPDATE files SET filename = ?, path = ? WHERE id = ?", (safe_name, new_path, file_id))
    conn.commit(); conn.close(); log("rename_file", f"{file_id}->{safe_name}")
    return jsonify({"ok":True,"filename":safe_name})

@app.route("/api/files/<file_id>/move", methods=["POST"])
def api_move_file(file_id):
    data = request.json or {}
    dest_folder = data.get("folder")
    if not dest_folder:
        return jsonify({"error":"folder required"}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT path,filename FROM files WHERE id = ?", (file_id,))
    f = cur.fetchone()
    if not f:
        conn.close(); return jsonify({"error":"file not found"}), 404
    cur.execute("SELECT path FROM folders WHERE id = ?", (dest_folder,))
    folder_row = cur.fetchone()
    if not folder_row:
        conn.close(); return jsonify({"error":"destination folder not found"}), 404
    old_path = f["path"]; filename = f["filename"]
    new_path = os.path.join(folder_row["path"], file_id + "_" + filename)
    try:
        shutil.move(old_path, new_path)
    except Exception as e:
        conn.close(); return jsonify({"error":"move_failed","detail":str(e)}), 500
    cur.execute("UPDATE files SET folder_id = ?, path = ? WHERE id = ?", (dest_folder, new_path, file_id))
    conn.commit(); conn.close(); log("move_file", f"{file_id}->folder:{dest_folder}")
    return jsonify({"ok":True})

# -------- Backup export/import --------
@app.route("/api/backup/export", methods=["GET"])
def api_export_backup():
    zname = os.path.join(BASE_DIR, f"backup_{int(datetime.utcnow().timestamp())}.zip")
    with zipfile.ZipFile(zname, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(DB_PATH): z.write(DB_PATH, os.path.basename(DB_PATH))
        for root, _, files in os.walk(DATA_DIR):
            for fn in files:
                full = os.path.join(root, fn); arc = os.path.relpath(full, BASE_DIR); z.write(full, arc)
    return send_file(zname, as_attachment=True)

@app.route("/api/backup/import", methods=["POST"])
def api_import_backup():
    f = request.files.get("file")
    if not f: return jsonify({"error":"file required"}), 400
    tmpzip = os.path.join(BASE_DIR, "tmp_import.zip"); f.save(tmpzip)
    with zipfile.ZipFile(tmpzip,"r") as z: z.extractall(BASE_DIR)
    os.remove(tmpzip); log("import_backup", f.filename); return jsonify({"ok":True})

# -------- Wallpaper (virtual desktop only) --------
@app.route("/api/settings/wallpaper", methods=["POST"])
def api_settings_wallpaper():
    # Accept file upload 'wallpaper' OR JSON {color:'#fff'}
    if 'wallpaper' in request.files:
        f = request.files['wallpaper']
        if f.filename == '':
            return jsonify({"error":"no file"}), 400
        safe = secure_filename(f.filename)
        dest_dir = os.path.join(DATA_DIR, "wallpapers")
        os.makedirs(dest_dir, exist_ok=True)
        dest_name = f"{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex}_{safe}"
        dest_path = os.path.join(dest_dir, dest_name)
        f.save(dest_path)
        rel_path = os.path.relpath(dest_path, BASE_DIR)
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("wallpaper_path", rel_path))
        cur.execute("DELETE FROM settings WHERE key = ?", ("wallpaper_color",))
        conn.commit(); conn.close(); log("set_wallpaper", rel_path)
        return jsonify({"ok":True,"path":rel_path})
    else:
        data = request.json or {}
        color = data.get("color")
        if not color:
            return jsonify({"error":"color or wallpaper file required"}), 400
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("wallpaper_color", color))
        cur.execute("DELETE FROM settings WHERE key = ?", ("wallpaper_path",))
        conn.commit(); conn.close(); log("set_wallpaper_color", color)
        return jsonify({"ok":True,"color":color})

@app.route("/_wallpaper/<path:rel>", methods=["GET"])
def serve_wallpaper(rel):
    path = os.path.join(BASE_DIR, rel)
    if os.path.exists(path):
        return send_file(path)
    return ('', 404)

# -------- Logs & Settings --------
@app.route("/api/logs", methods=["GET"])
def api_logs():
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT * FROM logs ORDER BY ts DESC LIMIT 500")
    rows = [dict(r) for r in cur.fetchall()]; conn.close(); return jsonify(rows)

@app.route("/api/settings", methods=["GET","POST"])
def api_settings():
    conn = get_conn(); cur = conn.cursor()
    if request.method == "GET":
        cur.execute("SELECT key,value FROM settings"); rows = {r["key"]: r["value"] for r in cur.fetchall()}; conn.close(); return jsonify(rows)
    else:
        data = request.json or {}
        for k,v in data.items():
            cur.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k,str(v)))
        conn.commit(); conn.close(); return jsonify({"ok":True})

# -------- GDPR erase all data --------
@app.route("/api/gdpr/erase", methods=["POST"])
def api_gdpr_erase():
    conn = get_conn(); cur = conn.cursor()
    # remove files
    for root, _, files in os.walk(DATA_DIR):
        for fn in files:
            try: os.remove(os.path.join(root, fn))
            except Exception: pass
    # remove directories
    for entry in os.listdir(DATA_DIR):
        path = os.path.join(DATA_DIR, entry)
        if os.path.isdir(path):
            try: shutil.rmtree(path)
            except Exception: pass
    cur.execute("DELETE FROM files"); cur.execute("DELETE FROM folders"); cur.execute("DELETE FROM logs"); conn.commit(); conn.close()
    ensure_root_folder(); log("gdpr_erase", "all")
    return jsonify({"ok":True})

# -------- System info (extended) --------
@app.route("/api/system/extended", methods=["GET"])
def api_system_extended():
    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version()
    }
    try:
        import time
        info["uptime_seconds"] = None
        if psutil:
            info["cpu_count_logical"] = psutil.cpu_count(logical=True)
            info["cpu_count_physical"] = psutil.cpu_count(logical=False)
            info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory(); disk = psutil.disk_usage(BASE_DIR)
            info.update({
                "total_ram": mem.total,
                "available_ram": mem.available,
                "disk_total": disk.total,
                "disk_used": disk.used,
                "disk_free": disk.free
            })
            try:
                boot = psutil.boot_time()
                info["uptime_seconds"] = int(time.time() - boot)
            except Exception:
                pass
            try:
                net = psutil.net_if_stats()
                info["net_if_count"] = len(net)
            except Exception:
                pass
    except Exception:
        pass
    return jsonify(info)




































# -------- Global Search Engine API --------
import requests
from bs4 import BeautifulSoup

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    source = request.args.get("sources", "web")

    results = {
        "local": [],
        "web": [],
        "youtube": []
    }

    if not q:
        return jsonify(results)

    conn = get_conn(); cur = conn.cursor()

    # -------------------------
    # LOCAL SEARCH (Your OS files)
    # -------------------------
    if source in ("local", "all"):
        cur.execute("SELECT id, filename FROM files WHERE filename LIKE ?", (f"%{q}%",))
        rows = cur.fetchall()
        results["local"] = [
            {"id": r["id"], "filename": r["filename"]}
            for r in rows
        ]

    # -------------------------
    # WEB SEARCH (DuckDuckGo, no API key)
    # -------------------------
    if source in ("web", "all"):
        try:
            url = f"https://duckduckgo.com/html/?q={q}"
            html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text
            soup = BeautifulSoup(html, "html.parser")

            web_out = []
            for a in soup.select(".result__a")[:7]:
                title = a.get_text()
                link = a.get("href")
                snippet_tag = a.find_parent("div").find_next("a", class_="result__snippet")
                snippet = snippet_tag.get_text() if snippet_tag else ""

                web_out.append({
                    "title": title,
                    "link": link,
                    "snippet": snippet
                })

            results["web"] = web_out

        except Exception as e:
            results["web"] = [{"error": str(e)}]

    # -------------------------
    # YOUTUBE SEARCH (no-key JSON mirror)
    # -------------------------
    if source in ("youtube", "all"):
        try:
            yt = requests.get(f"https://ytsearch.blob.core.windows.net/json/{q}.json").json()
            vids = []
            for item in yt.get("videos", [])[:5]:
                vids.append({
                    "title": item["title"],
                    "embed": f"https://www.youtube.com/embed/{item['id']}"
                })
            results["youtube"] = vids
        except:
            results["youtube"] = []

    conn.close()
    return jsonify(results)








# -------- Serve frontend --------
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    full = os.path.join(FRONTEND_DIR, path)
    if path and os.path.exists(full):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, "index.html")








if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)




