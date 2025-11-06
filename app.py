# app.py
import os
import sqlite3
import uuid
import datetime
import io
import subprocess
import random
from functools import wraps
from flask import (
    Flask, request, session, redirect, url_for, jsonify,
    send_from_directory, render_template_string, flash
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageDraw, ImageFont

# Optional moviepy for thumbnail extraction
try:
    from moviepy.editor import VideoFileClip
    MOVIEPY = True
except Exception:
    MOVIEPY = False

# ---------------- Config ----------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "emotube.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
THUMBS_DIR = os.path.join(UPLOADS_DIR, "thumbs")
AVATARS_DIR = os.path.join(UPLOADS_DIR, "avatars")

for d in (STATIC_DIR, UPLOADS_DIR, THUMBS_DIR, AVATARS_DIR):
    os.makedirs(d, exist_ok=True)

ALLOWED_VIDEO = {"mp4", "webm", "ogg", "mov", "mkv"}
ALLOWED_IMAGE = {"png", "jpg", "jpeg", "gif"}

# Admin credentials requested
ADMIN_EMAIL = "eminomer906@gmail.com"
ADMIN_PASSWORD = "emin1234sensin"

app = Flask(__name__)
app.secret_key = os.environ.get("EMO_SECRET", "emotube_dev_secret_key")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

# ---------------- DB helpers & init (sıfırdan) ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def recreate_db():
    # remove DB if exists (user asked "sıfırdan")
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print("Eski veritabanı silindi, yeni DB oluşturuluyor.")
        except Exception as e:
            print("DB silme hatası:", e)
    db = get_db()
    db.executescript("""
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash TEXT,
        display_name TEXT,
        bio TEXT,
        avatar TEXT,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        description TEXT,
        filename TEXT,
        thumb TEXT,
        views INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER,
        user_id INTEGER,
        text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER,
        user_id INTEGER,
        is_like INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(video_id,user_id)
    );
    CREATE TABLE subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subscriber_id INTEGER,
        channel_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(subscriber_id,channel_id)
    );
    CREATE TABLE history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        video_id INTEGER,
        watched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # create admin user
    try:
        db.execute("INSERT INTO users (username,password_hash,display_name,is_admin) VALUES (?,?,?,1)",
                   (ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), "Admin"))
        db.commit()
        print("Admin oluşturuldu:", ADMIN_EMAIL)
    except Exception as e:
        print("Admin oluşturma hatası:", e)
    db.close()

# create fresh DB
recreate_db()

# ---------------- Utilities ----------------
def allowed_file(filename, allowed_set):
    return "." in filename and filename.rsplit(".",1)[1].lower() in allowed_set

def save_file(file_storage, dest_dir, allowed_set):
    if not file_storage:
        return None
    filename = secure_filename(file_storage.filename)
    if not allowed_file(filename, allowed_set):
        return None
    ext = filename.rsplit(".",1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(dest_dir, new_name)
    file_storage.save(path)
    return new_name

def make_placeholder(text, out_path, size=(640,360), bgcolor=(90,30,120)):
    try:
        img = Image.new("RGB", size, bgcolor)
        d = ImageDraw.Draw(img)
        try:
            f = ImageFont.truetype("arial.ttf", 28)
        except:
            f = ImageFont.load_default()
        w,h = d.textsize(text, font=f)
        d.text(((size[0]-w)/2,(size[1]-h)/2), text, font=f, fill=(255,255,255))
        img.save(out_path)
        return True
    except Exception as e:
        print("placeholder err:", e)
        return False

def extract_frame_moviepy(video_path, out_path):
    try:
        clip = VideoFileClip(video_path)
        t = min(1.0, max(0.5, clip.duration/2.0)) if clip.duration>0 else 0.5
        frame = clip.get_frame(t)
        img = Image.fromarray(frame)
        img.thumbnail((640,360))
        img.save(out_path)
        clip.reader.close()
        if clip.audio:
            try:
                clip.audio.reader.close_proc()
            except:
                pass
        return True
    except Exception as e:
        print("moviepy error:", e)
        return False

def extract_frame_ffmpeg(video_path, out_path):
    cmd = ['ffmpeg','-y','-ss','00:00:01','-i', video_path, '-frames:v','1','-q:v','2', out_path]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0 and os.path.exists(out_path)
    except Exception as e:
        print("ffmpeg error:", e)
        return False

def create_thumbnail(video_filename, title):
    video_path = os.path.join(UPLOADS_DIR, video_filename)
    thumb_name = f"{uuid.uuid4().hex}.png"
    out_path = os.path.join(THUMBS_DIR, thumb_name)
    # try moviepy
    if MOVIEPY:
        if extract_frame_moviepy(video_path, out_path):
            return thumb_name
    # try ffmpeg
    if extract_frame_ffmpeg(video_path, out_path):
        return thumb_name
    # fallback
    make_placeholder(title[:24] or "EmoTube99", out_path)
    return thumb_name

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    r = db.execute("SELECT id,username,display_name,avatar,is_admin FROM users WHERE id=?", (uid,)).fetchone()
    db.close()
    return r

def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("user_id"):
            return redirect(url_for("enter"))
        return fn(*a, **kw)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        if not u or u['is_admin'] != 1:
            flash("Admin yetkisi gerekli")
            return redirect(url_for("index"))
        return fn(*a, **kw)
    return wrapper

# ---------------- Single page HTML (base) ----------------
BASE_HTML = r"""
<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EmoTube99</title>
<style>
:root{--accent:#9b59ff;--muted:#cbbde6}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,Arial,'Poppins',sans-serif;background:linear-gradient(180deg,#0b0010,#2a0632);color:#fff}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:12px 18px;background:rgba(255,255,255,0.03);position:sticky;top:0;z-index:20}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:56px;height:44px}
.title{font-weight:800;color:var(--accent);font-size:20px}
.search{flex:1;margin:0 16px}
.search input{width:100%;padding:8px 12px;border-radius:999px;border:1px solid rgba(255,255,255,0.06);background:transparent;color:#fff}
.controls{display:flex;gap:10px;align-items:center}
.btn{background:var(--accent);border:none;padding:8px 12px;border-radius:8px;color:#fff;cursor:pointer}
.ghost{background:transparent;border:1px solid rgba(255,255,255,0.06);padding:6px 10px;border-radius:8px;color:var(--muted)}
.container{display:flex;gap:18px;padding:18px}
.sidebar{width:260px}
.card{background:linear-gradient(180deg,rgba(255,255,255,0.02),rgba(255,255,255,0.01));padding:12px;border-radius:10px}
.grid{flex:1;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}
.video{background:rgba(0,0,0,0.2);border-radius:8px;overflow:hidden}
.thumb{width:100%;height:150px;object-fit:cover;background:#220022}
.meta{padding:8px}
.meta h4{margin:0;font-size:16px}
.meta .by{font-size:12px;color:var(--muted);margin-top:6px}
.profile-avatar{width:36px;height:36px;border-radius:50%;background:linear-gradient(90deg,#b98bff,#7a3bff);overflow:hidden}
.modal{position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:80}
.panel{width:92%;max-width:920px;background:#08020a;padding:16px;border-radius:12px}
.closebtn{background:transparent;border:0;color:var(--muted);cursor:pointer;font-size:18px}
.small{font-size:13px;color:var(--muted)}
.splash{height:56vh;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px}
.spin{font-weight:900;font-size:40px;color:var(--accent);animation:float 3s ease-in-out infinite}
@keyframes float{0%{transform:translateY(0)}50%{transform:translateY(-8px)}100%{transform:translateY(0)}}
.form-input{width:100%;padding:8px;border-radius:8px;border:1px solid rgba(255,255,255,0.06);background:transparent;color:#fff}
.comment{background:rgba(255,255,255,0.03);padding:8px;border-radius:8px;margin-top:8px}
.hamb{width:36px;height:32px;display:inline-block;cursor:pointer}
.hamb div{height:3px;background:linear-gradient(90deg,var(--accent),#fff);margin:6px;border-radius:3px}
.hamb-actions{position:absolute;left:10px;top:64px;display:none;flex-direction:column;gap:8px}
.hamb-actions.show{display:flex}
.hamb-actions .action-btn{background:linear-gradient(90deg,#7a3bff,#b98bff);padding:8px 12px;border-radius:8px;border:none;color:white;cursor:pointer}
.notice{background:linear-gradient(90deg,#6b3bcc,#a37bff);padding:8px;border-radius:8px;margin-top:8px}
.admin-badge{background:#ff7b7b;color:#000;padding:4px 8px;border-radius:6px;font-weight:700}
.logout-btn{background:#ff5c5c;color:#fff;border:none;padding:6px 10px;border-radius:8px;cursor:pointer}
</style>
</head>
<body>

<header class="topbar">
  <div class="brand">
    <svg class="logo" viewBox="0 0 100 80" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#b98bff"/><stop offset="1" stop-color="#7a3bff"/></linearGradient></defs>
      <rect rx="12" width="100" height="80" fill="url(#g)"/><polygon points="36,22 66,40 36,58" fill="white"/>
    </svg>
    <div>
      <div class="title">EmoTube99 {% if user and user['is_admin'] %}<span class="admin-badge">ADMIN</span>{% endif %}</div>
      <div class="small">Morun en şık hali</div>
    </div>
  </div>

  <div style="position:relative">
    <div class="hamb" onclick="toggleHamb()"><div></div><div></div><div></div></div>
    <div id="hambActions" class="hamb-actions">
      <button class="action-btn" onclick="location.href='/subs'">Abonelikler</button>
      <button class="action-btn" onclick="location.href='/history'">İzleme Geçmişi</button>
      {% if user and user['is_admin'] %}
        <button class="action-btn" onclick="location.href='/admin'">Admin Panel</button>
      {% endif %}
    </div>
  </div>

  <div class="search">
    <form id="searchForm" onsubmit="event.preventDefault(); doSearch();">
      <input id="q" placeholder="Ne izlemek istersin? örn: komik kedi..." value="{{ request.args.get('q','') }}">
    </form>
  </div>

  <div class="controls">
    {% if user %}
      <button class="ghost" onclick="openUpload()">Yükle</button>
      <div style="display:flex;align-items:center;gap:8px;">
        <div style="cursor:pointer" onclick="location.href='/profile/{{ user['username'] }}'">
          <div class="profile-avatar">{% if user['avatar'] %}<img src="/uploads/{{ user['avatar'] }}" style="width:100%;height:100%;object-fit:cover">{% endif %}</div>
        </div>
        <div class="small" style="margin-right:8px">{{ user['username'] }}</div>
        <form method="post" action="/logout" style="display:inline">
          <button class="logout-btn">Çıkış</button>
        </form>
      </div>
    {% else %}
      <button class="btn" onclick="openAuth('login')">Giriş</button>
      <button class="btn" onclick="openAuth('register')">Kayıt</button>
    {% endif %}
  </div>
</header>

<main class="container">
  {% if not passed_captcha %}
    <div style="flex:1">
      <div class="splash card">
        <div class="spin">SSÇS ELBET BİR GÜN</div>
        <div class="small">Siteye devam etmeden önce doğrulama</div>
        <div style="margin-top:12px">
          <form method="post" action="/enter">
            <label style="font-size:20px;font-weight:700;margin-right:8px">{{ captcha_q }}</label>
            <input name="answer" class="form-input" style="width:120px;display:inline-block" />
            <button class="btn" style="margin-left:8px">Doğrula</button>
          </form>
        </div>
      </div>
    </div>
  {% else %}
    <div style="flex:1">
      <div style="margin-bottom:14px">
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div><strong>Yeni Videolar</strong></div>
            <div class="small">Toplam: {{ total_videos }}</div>
          </div>
        </div>
      </div>

      <div class="grid">
        {% for v in videos %}
        <div class="video card">
          <a href="javascript:openPlayer({{ v['id'] }})"><img class="thumb" src="{{ v['thumb_url'] }}"></a>
          <div class="meta">
            <h4>{{ v['title'] }}</h4>
            <div class="by">by <strong>{{ v['u_name'] }}</strong> • {{ v['created_at'][:16] }} • {{ v['views'] }} views</div>
            {% if user and (user['is_admin'] or user['id']==v['user_id']) %}
              <div style="margin-top:8px">
                <button class="ghost" onclick="deleteVideo({{ v['id'] }})">Videoyu Sil</button>
              </div>
            {% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
    </div>

    <aside class="sidebar">
      <div class="card">
        <strong>Hızlı</strong>
        <div class="small" style="margin-top:8px">Profil / Yükle / Abonelikler</div>
        <div style="margin-top:12px">
          {% if user %}
            <button class="ghost" onclick="openUpload()">Yükle</button>
            <button class="ghost" onclick="location.href='/profile/{{ user['username'] }}'">Profilim</button>
          {% else %}
            <button class="btn" onclick="openAuth('register')">Kayıt Ol</button>
          {% endif %}
        </div>
      </div>
      <div style="height:12px"></div>
      <div class="card">
        <strong>Arama</strong>
        <div class="small" style="margin-top:8px">Kelimeleri yazarak arama yapabilirsiniz.</div>
      </div>
    </aside>
  {% endif %}
</main>

<footer style="padding:14px;text-align:center;color:var(--muted)">&copy; EmoTube99 — Demo</footer>

<div id="modalRoot"></div>
<div id="noticeRoot" style="position:fixed;right:18px;top:90px;z-index:120"></div>

<script>
function toggleHamb(){ document.getElementById('hambActions').classList.toggle('show'); }
function doSearch(){ const q=document.getElementById('q').value; location.href='/?q='+encodeURIComponent(q); }
function notice(msg){ const n=document.createElement('div'); n.className='notice'; n.innerText=msg; document.getElementById('noticeRoot').appendChild(n); setTimeout(()=>n.remove(),3500); }

function openAuth(mode){
  const root=document.getElementById('modalRoot'); root.innerHTML='';
  const modal=document.createElement('div'); modal.className='modal';
  modal.innerHTML = `<div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h3>${mode==='login'?'Giriş Yap':'Kayıt Ol'}</h3><button class="closebtn" onclick="this.closest('.modal').remove()">✖</button>
    </div>
    <form id="authForm">
      <div style="margin-top:8px"><input name="username" class="form-input" placeholder="E-posta veya kullanıcı" required></div>
      <div style="margin-top:8px"><input type="password" name="password" class="form-input" placeholder="Şifre" required></div>
      ${mode==='register'?'<div style="margin-top:8px"><input name="display" class="form-input" placeholder="Gösterilecek isim (opsiyonel)"></div><div style="margin-top:8px"><label>Robot musun? 3+4 = ?</label><input name="captcha" class="form-input"></div>':''}
      <div style="margin-top:12px"><button class="btn" type="submit">${mode==='login'?'Giriş':'Kayıt'}</button></div>
    </form>
  </div>`;
  root.appendChild(modal);
  document.getElementById('authForm').addEventListener('submit', e=>{
    e.preventDefault();
    const fd=new FormData(e.target);
    fetch(mode==='login'?'/api/login':'/api/register',{method:'POST',body:fd}).then(r=>r.json()).then(j=>{
      if(j.ok){ notice('Başarılı — yönlendiriliyor...'); setTimeout(()=>location.reload(),700); } else notice(j.error||'Hata');
    });
  });
}

function openUpload(){
  const root=document.getElementById('modalRoot'); root.innerHTML='';
  const modal=document.createElement('div'); modal.className='modal';
  modal.innerHTML = `<div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center"><h3>Video Yükle</h3><button class="closebtn" onclick="this.closest('.modal').remove()">✖</button></div>
    <form id="upForm" enctype="multipart/form-data">
      <div style="margin-top:8px"><input type="text" name="title" class="form-input" placeholder="Başlık" required></div>
      <div style="margin-top:8px"><textarea name="description" class="form-input" placeholder="Açıklama"></textarea></div>
      <div style="margin-top:8px">Video dosyası: <input type="file" name="video" accept="video/*" required></div>
      <div id="uploadMsg" style="margin-top:12px"></div>
      <div style="margin-top:12px"><button class="btn">Yükle</button></div>
    </form>
  </div>`;
  root.appendChild(modal);

  document.getElementById('upForm').addEventListener('submit', e=>{
    e.preventDefault();
    const upBtn = e.target.querySelector('button');
    upBtn.disabled=true; upBtn.innerText='Yükleniyor...';
    const msgEl = document.getElementById('uploadMsg'); msgEl.innerHTML='';
    const fd = new FormData(e.target);
    fetch('/upload',{method:'POST',body:fd}).then(r=>r.json()).then(j=>{
      upBtn.disabled=false; upBtn.innerText='Yükle';
      if(j.ok){
        msgEl.innerHTML='<div class="notice">Video başarıyla yüklendi</div>';
        setTimeout(()=>{ location.reload(); },900);
      } else {
        msgEl.innerHTML='<div class="notice" style="background:#ff7b7b;color:#000">'+(j.error||'Yükleme hatası')+'</div>';
      }
    }).catch(err=>{
      upBtn.disabled=false; upBtn.innerText='Yükle';
      msgEl.innerHTML='<div class="notice" style="background:#ff7b7b;color:#000">Sunucu hatası</div>';
    });
  });
}

function openPlayer(id){
  fetch('/api/video/'+id).then(r=>r.json()).then(j=>{
    if(j.error) return notice(j.error||'Hata');
    const v=j.video;
    const root=document.getElementById('modalRoot'); root.innerHTML='';
    const modal=document.createElement('div'); modal.className='modal';
    modal.innerHTML = `<div class="panel">
      <div style="display:flex;justify-content:space-between;align-items:center"><h3>${v.title}</h3><button class="closebtn" onclick="this.closest('.modal').remove()">✖</button></div>
      <video controls style="width:100%;height:auto" autoplay><source src="/uploads/${v.filename}"></video>
      <p class="small">${v.description||''}</p>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn" onclick="like(${v.id})">Beğen</button>
        <button class="ghost" onclick="subscribe(${v.user_id})">Abone Ol</button>
        <div style="margin-left:auto" class="small">${v.views} izlenme</div>
      </div>
      <div style="margin-top:12px"><h4>Yorumlar</h4><div id="cmts"></div>
        <div style="margin-top:8px"><textarea id="cmttext" class="form-input" placeholder="Yorum yaz..."></textarea><br><button class="btn" onclick="postComment(${v.id})">Yorum Gönder</button></div>
      </div>
    </div>`;
    root.appendChild(modal);
    loadComments(v.id);
    fetch('/api/record_history',{method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'video_id='+v.id});
  }).catch(()=>notice('Video yüklenemiyor'));
}

function loadComments(vid){
  fetch('/api/comments/'+vid).then(r=>r.json()).then(j=>{
    const cdiv=document.getElementById('cmts'); if(!cdiv) return;
    cdiv.innerHTML='';
    j.comments.forEach(c=>{
      const el=document.createElement('div'); el.className='comment';
      el.innerHTML=`<strong>${c.username}</strong> <div class="small">${c.created_at}</div><div>${c.text}</div>`;
      cdiv.appendChild(el);
    });
  });
}
function postComment(vid){
  const t=document.getElementById('cmttext');
  if(!t || !t.value.trim()) return notice('Yorum girin');
  fetch('/comment',{method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'video_id='+vid+'&text='+encodeURIComponent(t.value)})
    .then(()=>{ t.value=''; loadComments(vid); notice('Yorum eklendi'); });
}
function like(id){ fetch('/like',{method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'video_id='+id+'&type=like'}).then(()=>notice('Beğenildi')) }
function subscribe(cid){ fetch('/subscribe',{method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'channel_id='+cid}).then(()=>location.reload()) }

function deleteVideo(id){
  if(!confirm('Bu videoyu silmek istediğine emin misin?')) return;
  fetch('/delete_video/'+id,{method:'POST'}).then(()=>location.reload());
}
</script>
</body>
</html>
"""

# ---------------- Static helpers ----------------
@app.route("/uploads/<path:filename>")
def serve_uploads(filename):
    return send_from_directory(UPLOADS_DIR, filename)

@app.route("/uploads/thumbs/<path:filename>")
def serve_thumbs(filename):
    return send_from_directory(THUMBS_DIR, filename)

@app.route("/uploads/avatars/<path:filename>")
def serve_avatars(filename):
    return send_from_directory(AVATARS_DIR, filename)

# ---------------- Entry / captcha ----------------
@app.route("/enter", methods=["GET","POST"])
def enter():
    if request.method == "POST":
        try:
            ans = int(request.form.get("answer","0"))
            if ans == session.get("captcha_ans"):
                session["passed_captcha"] = True
                return redirect(url_for("index"))
            else:
                flash("Doğrulama hatalı")
        except:
            flash("Doğrulama hatalı")
    a = random.randint(2,9); b = random.randint(1,9)
    session["captcha_ans"] = a + b
    return render_template_string(BASE_HTML, user=current_user(), passed_captcha=False, captcha_q=f"{a} + {b} = ?", videos=[], total_videos=0)

# ---------------- Index ----------------
@app.route("/")
def index():
    if not session.get("passed_captcha"):
        return redirect(url_for("enter"))
    q = request.args.get("q","").strip().lower()
    db = get_db()
    if q:
        rows = db.execute("""SELECT v.*, u.username as u_name FROM videos v JOIN users u ON v.user_id=u.id
                             WHERE lower(v.title) LIKE ? OR lower(v.description) LIKE ? OR lower(u.username) LIKE ?
                             ORDER BY v.created_at DESC LIMIT 200""", (f"%{q}%",f"%{q}%",f"%{q}%")).fetchall()
    else:
        rows = db.execute("""SELECT v.*, u.username as u_name FROM videos v JOIN users u ON v.user_id=u.id
                             ORDER BY v.created_at DESC LIMIT 200""").fetchall()
    videos = []
    for r in rows:
        thumb = r["thumb"] or ""
        thumb_url = ("/uploads/thumbs/"+thumb) if thumb else "/static_placeholder"
        videos.append({
            "id": r["id"], "title": r["title"], "description": r["description"],
            "filename": r["filename"], "thumb_url": thumb_url, "views": r["views"],
            "created_at": r["created_at"], "u_name": r["u_name"], "user_id": r["user_id"]
        })
    total = len(videos)
    db.close()
    return render_template_string(BASE_HTML, user=current_user(), passed_captcha=True, captcha_q="", videos=videos, total_videos=total)

# ---------------- Auth (AJAX) ----------------
@app.route("/api/register", methods=["POST"])
def api_register():
    username = request.form.get("username","").strip()
    password = request.form.get("password","")
    display = request.form.get("display","").strip() or username
    captcha = request.form.get("captcha","").strip()
    if not username or not password:
        return jsonify({"ok":False,"error":"Kullanıcı ve şifre gerekli"})
    # simple captcha for register (3+4)
    if captcha != "7":
        return jsonify({"ok":False,"error":"Robot doğrulaması yanlış"})
    db = get_db()
    try:
        pw = generate_password_hash(password)
        db.execute("INSERT INTO users(username,password_hash,display_name) VALUES(?,?,?)", (username,pw,display))
        db.commit()
        user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        session["user_id"] = user["id"]; session["username"] = username; session["passed_captcha"] = True
        db.close()
        return jsonify({"ok":True})
    except sqlite3.IntegrityError:
        db.close()
        return jsonify({"ok":False,"error":"Kullanıcı adı alınmış"})

@app.route("/api/login", methods=["POST"])
def api_login():
    username = request.form.get("username","").strip()
    password = request.form.get("password","")
    db = get_db()
    r = db.execute("SELECT id,password_hash,is_admin FROM users WHERE username=?", (username,)).fetchone()
    db.close()
    if not r or not check_password_hash(r["password_hash"], password):
        # also allow admin email login
        db = get_db()
        r2 = db.execute("SELECT id,password_hash,is_admin,username FROM users WHERE username=?", (username,)).fetchone()
        db.close()
        if not r2 or not check_password_hash(r2["password_hash"], password):
            return jsonify({"ok":False,"error":"Hatalı kullanıcı veya şifre"})
        else:
            session["user_id"] = r2["id"]; session["username"] = r2["username"]; session["passed_captcha"] = True
            return jsonify({"ok":True})
    session["user_id"] = r["id"]; session["username"] = username; session["passed_captcha"] = True
    return jsonify({"ok":True})

# admin login via modal - can use hardcoded creds too
@app.route("/admin-login", methods=["POST"])
def admin_login():
    email = request.form.get("email","").strip()
    password = request.form.get("password","")
    # check hardcoded admin (match earlier)
    if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
        # set session to admin DB user if present
        db = get_db()
        row = db.execute("SELECT id FROM users WHERE is_admin=1 LIMIT 1").fetchone()
        if row:
            session["user_id"] = row["id"]
            session["username"] = ADMIN_EMAIL
            session["passed_captcha"] = True
            db.close()
            return jsonify({"ok":True})
        db.close()
    # fallback: check DB is_admin
    db = get_db()
    r = db.execute("SELECT id,password_hash FROM users WHERE username=? AND is_admin=1", (email,)).fetchone()
    db.close()
    if not r or not check_password_hash(r["password_hash"], password):
        return jsonify({"ok":False,"error":"Admin kimlik doğrulama hatası"})
    session["user_id"] = r["id"]; session["passed_captcha"] = True
    return jsonify({"ok":True})

# ---------------- Upload ----------------
@app.route("/upload", methods=["POST"])
def upload():
    if not session.get("user_id"):
        return jsonify({"ok":False,"error":"Giriş yapın"}), 403
    title = request.form.get("title","Untitled").strip()
    desc = request.form.get("description","").strip()
    video_file = request.files.get("video")
    if not video_file:
        return jsonify({"ok":False,"error":"Video dosyası gerekli"}), 400
    if not allowed_file(video_file.filename, ALLOWED_VIDEO):
        return jsonify({"ok":False,"error":"Desteklenmeyen video biçimi"}), 400
    fname = save_file(video_file, UPLOADS_DIR, ALLOWED_VIDEO)
    if not fname:
        return jsonify({"ok":False,"error":"Video kaydedilemedi"}), 500
    # create thumbnail automatically
    try:
        thumbname = create_thumbnail(fname, title)
    except Exception as e:
        print("thumb create exception:", e)
        tnm = f"{uuid.uuid4().hex}.png"
        make_placeholder(title[:20], os.path.join(THUMBS_DIR, tnm))
        thumbname = tnm
    db = get_db()
    db.execute("INSERT INTO videos(user_id,title,description,filename,thumb) VALUES(?,?,?,?,?)",
               (session["user_id"], title, desc, fname, thumbname))
    db.commit()
    db.close()
    return jsonify({"ok":True})

# ---------------- API video detail ----------------
@app.route("/api/video/<int:vid>")
def api_video(vid):
    db = get_db()
    r = db.execute("SELECT v.*, u.username FROM videos v JOIN users u ON v.user_id=u.id WHERE v.id=?", (vid,)).fetchone()
    db.close()
    if not r:
        return jsonify({"error":"not found"}), 404
    return jsonify({"video":{
        "id": r["id"], "title": r["title"], "description": r["description"],
        "filename": r["filename"], "views": r["views"], "user_id": r["user_id"]
    }})

# ---------------- Comments / likes / subscribe ----------------
@app.route("/api/comments/<int:vid>")
def api_comments(vid):
    db = get_db()
    rows = db.execute("SELECT c.*, u.username FROM comments c JOIN users u ON c.user_id=u.id WHERE c.video_id=? ORDER BY c.created_at DESC", (vid,)).fetchall()
    db.close()
    out = [{"id":r["id"],"text":r["text"],"username":r["username"],"created_at":r["created_at"]} for r in rows]
    return jsonify({"comments": out})

@app.route("/comment", methods=["POST"])
def comment():
    if not session.get("user_id"):
        return redirect(url_for("enter"))
    vid = request.form.get("video_id")
    text = request.form.get("text","").strip()
    if not text:
        flash("Yorum boş")
        return redirect(url_for("index"))
    db = get_db()
    db.execute("INSERT INTO comments(video_id,user_id,text) VALUES(?,?,?)", (vid, session["user_id"], text))
    db.commit()
    db.close()
    return ("",204)

@app.route("/like", methods=["POST"])
def like():
    if not session.get("user_id"):
        return jsonify({"error":"login"}), 403
    vid = request.form.get("video_id")
    typ = request.form.get("type","like")
    db = get_db()
    try:
        db.execute("INSERT OR REPLACE INTO likes(video_id,user_id,is_like,created_at) VALUES(?,?,?,CURRENT_TIMESTAMP)",
                   (vid, session["user_id"], 1 if typ=="like" else 0))
        db.commit()
    except Exception:
        pass
    db.close()
    return ("",204)

@app.route("/subscribe", methods=["POST"])
def subscribe():
    if not session.get("user_id"):
        flash("Giriş yapın"); return redirect(url_for("enter"))
    cid = request.form.get("channel_id")
    db = get_db()
    cur = db.execute("SELECT id FROM subscriptions WHERE subscriber_id=? AND channel_id=?", (session["user_id"], cid)).fetchone()
    if cur:
        db.execute("DELETE FROM subscriptions WHERE id=?", (cur["id"],))
        flash("Abonelik iptal edildi")
    else:
        db.execute("INSERT INTO subscriptions(subscriber_id,channel_id) VALUES(?,?)", (session["user_id"], cid))
        flash("Abone olundu")
    db.commit()
    db.close()
    return redirect(request.referrer or url_for("index"))

# ---------------- History / profile / channel ----------------
@app.route("/api/record_history", methods=["POST"])
def api_record_history():
    vid = request.form.get("video_id")
    if not vid:
        return ("",204)
    db = get_db()
    db.execute("INSERT INTO history(user_id,video_id) VALUES(?,?)", (session.get("user_id"), vid))
    db.commit(); db.close()
    return ("",204)

@app.route("/profile/<username>")
def profile(username):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        db.close(); flash("Kullanıcı yok"); return redirect(url_for("index"))
    vids = db.execute("SELECT * FROM videos WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()
    videos = []
    for v in vids:
        videos.append({"id":v["id"], "title":v["title"], "thumb_url":("/uploads/thumbs/"+v["thumb"]) if v["thumb"] else "/static_placeholder", "created_at":v["created_at"], "views":v["views"]})
    subs = db.execute("SELECT COUNT(*) as c FROM subscriptions WHERE channel_id=?", (user["id"],)).fetchone()["c"]
    db.close()
    return render_template_string(BASE_HTML, user=current_user(), passed_captcha=True, captcha_q="", videos=videos, total_videos=len(videos), profile_user=user, subs_count=subs)

@app.route("/profile")
def my_profile():
    if not session.get("user_id"): return redirect(url_for("enter"))
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    db.close()
    return redirect(url_for("profile", username=u["username"]))

@app.route("/edit_profile", methods=["GET","POST"])
@login_required
def edit_profile():
    db = get_db()
    if request.method=="POST":
        display = request.form.get("display_name","").strip()
        bio = request.form.get("bio","").strip()
        avatar = request.files.get("avatar")
        avn = None
        if avatar and allowed_file(avatar.filename, ALLOWED_IMAGE):
            avn = save_file(avatar, AVATARS_DIR, ALLOWED_IMAGE)
        if avn:
            db.execute("UPDATE users SET display_name=?, bio=?, avatar=? WHERE id=?", (display,bio,avn,session["user_id"]))
        else:
            db.execute("UPDATE users SET display_name=?, bio=? WHERE id=?", (display,bio,session["user_id"]))
        db.commit()
        db.close()
        flash("Profil güncellendi")
        return redirect(url_for("profile", username=session.get("username")))
    u = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    db.close()
    html = "<h2>Profil düzenle</h2><form method='post' enctype='multipart/form-data'><input name='display_name' placeholder='Gösterilecek isim' value='{}'><br><textarea name='bio' placeholder='Bio'>{}</textarea><br><input type='file' name='avatar' accept='image/*'><br><button>Kaydet</button></form>".format(u["display_name"] or "", u["bio"] or "")
    return render_template_string(BASE_HTML + html, user=current_user(), passed_captcha=True, captcha_q="", videos=[], total_videos=0)

# ---------------- delete video (owner or admin) ----------------
@app.route("/delete_video/<int:vid>", methods=["POST"])
@login_required
def delete_video(vid):
    db = get_db()
    v = db.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not v:
        db.close(); flash("Video bulunamadı"); return redirect(url_for("index"))
    user = current_user()
    if user['is_admin']==1 or v['user_id']==user['id']:
        # remove files
        try:
            if v['filename']:
                fpath = os.path.join(UPLOADS_DIR, v['filename'])
                if os.path.exists(fpath): os.remove(fpath)
            if v['thumb']:
                tpath = os.path.join(THUMBS_DIR, v['thumb'])
                if os.path.exists(tpath): os.remove(tpath)
        except Exception as e:
            print("file remove err", e)
        db.execute("DELETE FROM videos WHERE id=?", (vid,))
        db.commit(); db.close()
        flash("Video silindi")
        return ("",204)
    else:
        db.close(); flash("Bu videoyu silmeye yetkin yok"); return redirect(url_for("index"))

# ---------------- delete account ----------------
@app.route("/delete_account", methods=["POST"])
@login_required
def delete_account():
    db = get_db()
    uid = session["user_id"]
    vids = db.execute("SELECT * FROM videos WHERE user_id=?", (uid,)).fetchall()
    for v in vids:
        try:
            if v["filename"]:
                f = os.path.join(UPLOADS_DIR, v["filename"])
                if os.path.exists(f): os.remove(f)
            if v["thumb"]:
                t = os.path.join(THUMBS_DIR, v["thumb"])
                if os.path.exists(t): os.remove(t)
        except:
            pass
    db.execute("DELETE FROM videos WHERE user_id=?", (uid,))
    db.execute("DELETE FROM comments WHERE user_id=?", (uid,))
    db.execute("DELETE FROM likes WHERE user_id=?", (uid,))
    db.execute("DELETE FROM subscriptions WHERE subscriber_id=? OR channel_id=?", (uid,uid))
    db.execute("DELETE FROM history WHERE user_id=?", (uid,))
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit(); db.close()
    session.clear()
    flash("Hesabınız silindi")
    return redirect(url_for("enter"))

# ---------------- subs / history ----------------
@app.route("/subs")
@login_required
def subs():
    db = get_db()
    rows = db.execute("""SELECT u.* FROM subscriptions s JOIN users u ON s.channel_id=u.id WHERE s.subscriber_id=?""", (session["user_id"],)).fetchall()
    subs = [{"id":r["id"], "username":r["username"], "display":r["display_name"]} for r in rows]
    db.close()
    body = "<h2>Abonelikler</h2>"
    for s in subs:
        body += f"<div><a href='/profile/{s['username']}'>{s['display'] or s['username']}</a></div>"
    return render_template_string(BASE_HTML + body, user=current_user(), passed_captcha=True, captcha_q="", videos=[], total_videos=0)

@app.route("/history")
@login_required
def history_page():
    db = get_db()
    rows = db.execute("SELECT h.*, v.title FROM history h JOIN videos v ON h.video_id=v.id WHERE h.user_id=? ORDER BY h.watched_at DESC LIMIT 200", (session["user_id"],)).fetchall()
    db.close()
    body = "<h2>İzleme Geçmişi</h2>"
    for r in rows:
        body += f"<div><a href='javascript:openPlayer({r['video_id']})'>{r['title']}</a> — {r['watched_at']}</div>"
    return render_template_string(BASE_HTML + body, user=current_user(), passed_captcha=True, captcha_q="", videos=[], total_videos=0)

# ---------------- watch route ----------------
@app.route("/watch/<int:vid>")
def watch_route(vid):
    db = get_db()
    r = db.execute("SELECT id FROM videos WHERE id=?", (vid,)).fetchone()
    db.close()
    if not r:
        flash("Video yok"); return redirect(url_for("index"))
    db = get_db(); db.execute("UPDATE videos SET views = views + 1 WHERE id=?", (vid,)); db.commit(); db.close()
    return redirect(url_for("index"))

@app.route("/static_placeholder")
def static_placeholder():
    buf = io.BytesIO()
    img = Image.new("RGB",(640,360),(90,30,120))
    d = ImageDraw.Draw(img)
    try:
        fnt = ImageFont.truetype("arial.ttf", 28)
    except:
        fnt = ImageFont.load_default()
    text = "EmoTube99"
    w,h = d.textsize(text, font=fnt)
    d.text(((640-w)/2,(360-h)/2), text, font=fnt, fill=(255,255,255))
    img.save(buf, "PNG")
    buf.seek(0)
    return (buf.getvalue(), 200, {"Content-Type":"image/png"})

# ---------------- Admin panel ----------------
@app.route("/admin")
@admin_required
def admin_panel():
    db = get_db()
    users_list = db.execute("SELECT id,username,display_name,is_admin,created_at FROM users ORDER BY created_at DESC").fetchall()
    vids = db.execute("SELECT v.id,v.title,u.username,v.created_at FROM videos v JOIN users u ON v.user_id=u.id ORDER BY v.created_at DESC").fetchall()
    db.close()
    body = "<h2>Admin Panel</h2><h3>Kullanıcılar</h3><div>"
    for u in users_list:
        body += f"<div style='padding:8px;border-bottom:1px solid #222'><strong>{u['username']}</strong> {u['display_name'] or ''} {'(ADMIN)' if u['is_admin'] else ''} <form method='post' style='display:inline' action='/admin/delete_user'><input type='hidden' name='user_id' value='{u['id']}'><button style='margin-left:8px;background:#ff7b7b;color:#000'>Sil</button></form></div>"
    body += "</div><h3>Videolar</h3><div>"
    for v in vids:
        body += f"<div style='padding:8px;border-bottom:1px solid #222'><strong>{v['title']}</strong> by {v['username']} <form method='post' style='display:inline' action='/admin/delete_video'><input type='hidden' name='video_id' value='{v['id']}'><button style='margin-left:8px;background:#ff7b7b;color:#000'>Sil</button></form></div>"
    body += "</div>"
    return render_template_string(BASE_HTML + body, user=current_user(), passed_captcha=True, captcha_q="", videos=[], total_videos=0)

@app.route("/admin/delete_video", methods=["POST"])
@admin_required
def admin_delete_video():
    vid = request.form.get("video_id")
    db = get_db()
    row = db.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if row:
        try:
            if row["filename"]:
                p = os.path.join(UPLOADS_DIR, row["filename"])
                if os.path.exists(p): os.remove(p)
            if row["thumb"]:
                t = os.path.join(THUMBS_DIR, row["thumb"])
                if os.path.exists(t): os.remove(t)
        except Exception as e:
            print("file remove err", e)
        db.execute("DELETE FROM videos WHERE id=?", (vid,))
        db.commit()
        db.close()
        flash("Video silindi")
        return redirect(url_for("admin_panel"))
    db.close()
    flash("Video bulunamadı"); return redirect(url_for("admin_panel"))

@app.route("/admin/delete_user", methods=["POST"])
@admin_required
def admin_delete_user():
    uid = request.form.get("user_id")
    db = get_db()
    vids = db.execute("SELECT * FROM videos WHERE user_id=?", (uid,)).fetchall()
    for v in vids:
        try:
            if v["filename"]:
                f = os.path.join(UPLOADS_DIR, v["filename"])
                if os.path.exists(f): os.remove(f)
            if v["thumb"]:
                t = os.path.join(THUMBS_DIR, v["thumb"])
                if os.path.exists(t): os.remove(t)
        except:
            pass
    db.execute("DELETE FROM videos WHERE user_id=?", (uid,))
    db.execute("DELETE FROM comments WHERE user_id=?", (uid,))
    db.execute("DELETE FROM likes WHERE user_id=?", (uid,))
    db.execute("DELETE FROM subscriptions WHERE subscriber_id=? OR channel_id=?", (uid,uid))
    db.execute("DELETE FROM history WHERE user_id=?", (uid,))
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    db.close()
    flash("Kullanıcı ve ilişkili veriler silindi")
    return redirect(url_for("admin_panel"))

# ---------------- Login / logout (simple forms) ----------------
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("enter"))

# ---------------- Run ----------------
if __name__ == "__main__":
    print("EmoTube99 başlatılıyor — http://127.0.0.1:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)

