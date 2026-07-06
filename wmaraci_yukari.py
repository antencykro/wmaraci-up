# -*- coding: utf-8 -*-
# WMARACI ILAN YUKARI TASIMA - Telegram bildirimli, cok ilan destekli.
# R10 otomasyonunun (r10_yukari.py) wmaraci uyarlamasi. Bulutta (GitHub Actions)
# calisir -> BILGISAYAR KAPALI olsa bile konu yukari tasinir.
#
# FARK (r10 -> wmaraci):
#   - r10: GET up.php (HTML cevap).  wmaraci: POST /api/forum/moveUpThread (JSON cevap).
#   - Limit: wmaraci her ILAN icin 30 DK (r10 saatte 1 kullanici basina).
#   - Cevap JSON: {"status":"success"|..., "title": <mesaj>}.
#     "title" basarisizken kalan sureyi verir: "...0 saat, 26 dakika, 14 saniye beklemeniz...".
#
# Calistirma:
#   python wmaraci_yukari.py          -> normal (zamanlayici/Actions boyle cagirir)
#   python wmaraci_yukari.py test     -> test: sonuc ne olursa Telegram'a yaz, sure bekleme

import os, sys, io, json, gzip, re, random, time
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

TR = timezone(timedelta(hours=3))   # Turkiye saati (bulutta UTC yerine bunu goster)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ---- Ayarlar: once ortam degiskeni (GitHub Secret), yoksa yerel config ----
try:
    import config_wmaraci as _F    # yerel ayar (sadece bu PC'de; repoda yok)
except Exception:
    _F = None


def _get(env, attr, default=None, cast=str):
    v = os.environ.get(env)
    if v not in (None, ""):
        v = v.strip().lstrip("﻿")
        try: return cast(v)
        except Exception: return v
    if _F is not None and hasattr(_F, attr):
        return getattr(_F, attr)
    return default


def _bool(env, attr, default):
    v = os.environ.get(env)
    if v not in (None, ""):
        return v.strip().lower() in ("1", "true", "yes", "evet", "on")
    if _F is not None and hasattr(_F, attr):
        return getattr(_F, attr)
    return default


_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

SITE = _get("WMARACI_SITE", "SITE", default="https://wmaraci.com").rstrip("/")
ENDPOINT = SITE + "/api/forum/moveUpThread"

# Hedef ilan(lar): bulutta env ile (virgullu), yerelde config listesi.
_env_ids = os.environ.get("WMARACI_THREAD_IDS") or os.environ.get("WMARACI_THREAD_ID")
if _env_ids:
    THREAD_IDS = [x.strip() for x in _env_ids.replace(" ", "").split(",") if x.strip()]
elif _F is not None and hasattr(_F, "THREAD_IDS"):
    THREAD_IDS = [str(x) for x in _F.THREAD_IDS]
else:
    THREAD_IDS = []

BASLIK = "Wmaracı Yukarı Taşıma"   # Telegram mesaj basligi (R10 kanaliyla karismaz)
STATE = "wmaraci-state.json"
LOG = "wmaraci-log.txt"
# wmaraci sunucu limiti: ilan basina 30 dk. Zamanlama (kullanici istegi):
#   BASARILI tasima  -> sonraki tetik 31 dk sonra (30 limitin 1 dk uzeri guvenli)
#   HATA/oturum/cf    -> sonraki tetik 2 dk sonra
#   TOO_EARLY         -> cevaptaki kalan sure kadar sonra
BUMP_INTERVAL_MIN = int(_get("WMARACI_BUMP_INTERVAL_MIN", "BUMP_INTERVAL_MIN", default="30", cast=int))
GATE_MIN = BUMP_INTERVAL_MIN                 # client kapisi: bundan once istek atma (bos istek olmasin)
NEXT_OK_MIN = BUMP_INTERVAL_MIN + 1          # basarili -> sonraki tetik (31 dk)
RETRY_MIN = int(_get("WMARACI_RETRY_AFTER_MIN", "RETRY_AFTER_MIN", default="5", cast=int))   # gecici hata -> 5 dk sonra tekrar
# GECICI hata (Cloudflare/baglanti) olursa AYNI is icinde RETRY_MIN dk bekleyip kac kez daha
# denensin. Kalici hata (AUTH/oturum) beklemez -> zaten aninda relogin denenir; bosuna dakika
# yakmamak icin uyku yok. 0 = retry kapali (sadece bir sonraki 30 dk'lik tetikte tekrar denenir).
MAX_RETRIES = int(_get("WMARACI_MAX_RETRIES", "MAX_RETRIES", default="1", cast=int))
_TRANSIENT = ("CLOUDFLARE", "ERROR")   # bunlarda 5 dk bekleyip tekrar dene (gecici olabilir)
# Tetik 30 dk penceresinden en fazla bu kadar ONCE gelirse, atlamak yerine kalani is icinde
# bekle (pencere acilinca tasi). Tetik genelde saniyeler once gelir; 4 dk fazlasiyla yeter.
GATE_WAIT_MAX = int(_get("WMARACI_GATE_WAIT_MAX_MIN", "GATE_WAIT_MAX_MIN", default="4", cast=int))
TEST = (len(sys.argv) > 1 and sys.argv[1].lower() == "test") \
    or os.environ.get("WMARACI_TEST", "").strip().lower() in ("1", "true", "yes")

COOKIE = _get("WMARACI_COOKIE", "COOKIE", default="")
# Oto yeniden-giris: cookie dusunce telefon+sifre ile login() taze cookie alir (captcha/SMS yok).
# DIKKAT: wmaraci 'email' alanina TELEFON bekler (telefonla giris). No secret'ta (WMARACI_PHONE).
LOGIN_PHONE = _get("WMARACI_PHONE", "LOGIN_PHONE", default="") \
    or _get("WMARACI_EMAIL", "LOGIN_EMAIL", default="")
LOGIN_PASSWORD = _get("WMARACI_PASSWORD", "LOGIN_PASSWORD", default="")
USER_AGENT = _get("WMARACI_UA", "USER_AGENT", default=_DEFAULT_UA)
TG_TOKEN = _get("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", default="")
TG_CHAT = _get("TG_CHAT_ID", "TELEGRAM_CHAT_ID", default="")
NOTIFY_SUCCESS = _bool("NOTIFY_SUCCESS", "NOTIFY_SUCCESS", True)
NOTIFY_ERROR = _bool("NOTIFY_ERROR", "NOTIFY_ERROR", True)
NOTIFY_TOO_EARLY = _bool("NOTIFY_TOO_EARLY", "NOTIFY_TOO_EARLY", False)


def now():
    return datetime.now(TR).strftime("%Y-%m-%d %H:%M:%S")


def logla(msg):
    line = f"[{now()}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def telegram(text):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT, "text": text, "disable_web_page_preview": "true",
        }).encode()
        with urllib.request.urlopen(url, data=data, timeout=30) as r:
            res = json.load(r)
        if not res.get("ok"):
            logla(f"Telegram HATA: {res}")
    except Exception as e:
        logla(f"Telegram gonderilemedi: {e}")


def tg_msg(ikon, tid, durum):
    return f"{ikon} {BASLIK}\n\nKonu: #{tid}\nDurum: {durum}\nSaat: {now()}"


# ---- State: her ilan icin son tasima zamani ----
def state_oku():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def state_yaz(d):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logla(f"State yazilamadi: {e}")


# ---- Oto yeniden-giris (cookie dusunce telefon+sifre ile) ----
def login():
    """Telefon+sifre ile /api/auth/login -> taze cookie string (PHPSESSID + loginHash) veya None.
    wmaraci 'email' alanina TELEFON bekler; rememberme=true loginHash'i kalici yapar (captcha/SMS yok)."""
    if not LOGIN_PHONE or not LOGIN_PASSWORD:
        return None
    import http.cookiejar
    body = json.dumps({"email": LOGIN_PHONE, "password": LOGIN_PASSWORD,
                       "rememberme": True}, ensure_ascii=False).encode()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(SITE + "/api/auth/login", data=body, method="POST", headers={
        "User-Agent": USER_AGENT, "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "*/*", "Origin": SITE, "Referer": SITE + "/"})
    try:
        with opener.open(req, timeout=40) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:
        logla(f"login baglanti hatasi: {e}")
        return None
    try:
        data = json.loads(text)
    except Exception:
        data = {}
    if not (isinstance(data, dict) and data.get("status") == "success"):
        logla(f"login basarisiz: {text[:160]}")
        return None
    names = {c.name for c in jar}
    if "PHPSESSID" not in names or not any("login" in n.lower() for n in names):
        logla(f"login uyari: cookie eksik olabilir ({','.join(sorted(names)) or 'bos'})")
    cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
    return cookie or None


def relogin():
    """Global COOKIE'yi taze oturumla degistirir. Basarida True."""
    global COOKIE
    c = login()
    if c:
        COOKIE = c
        logla("Oturum yenilendi (telefon+sifre -> taze cookie).")
        return True
    return False


# ---- wmaraci moveUpThread istegi ----
def bump(tid):
    """(durum, ozet) doner. durum: SUCCESS | TOO_EARLY | CLOUDFLARE | AUTH | UNKNOWN | ERROR"""
    body = json.dumps({"threadId": str(tid)}).encode()
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "User-Agent": USER_AGENT,
        "Cookie": COOKIE,
        "Content-Type": "text/plain;charset=UTF-8",   # CANLI: Twix boyle yolluyor
        "Accept": "*/*",
        "Origin": SITE,
        "Referer": SITE + "/",
    })
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read()
            if "gzip" in (r.headers.get("Content-Encoding") or ""):
                try: raw = gzip.decompress(raw)
                except Exception: pass
            text = raw.decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
        try: text = e.read().decode("utf-8", "replace")
        except Exception: text = ""
        if status in (403, 503):
            return "CLOUDFLARE", f"HTTP {status} (Cloudflare/engel olabilir)"
    except Exception as e:
        return "ERROR", f"Baglanti hatasi: {e}"
    return _coz(status, text)


def _coz(status, text):
    low = text.lower()
    if status in (403, 503) or "just a moment" in low or "cf-mitigated" in low \
       or "attention required" in low or "cloudflare" in low:
        return "CLOUDFLARE", "Cloudflare engeli / cerez gecersiz"
    data = None
    try:
        data = json.loads(text)
    except Exception:
        pass
    if isinstance(data, dict):
        title = data.get("title") or ""
        if data.get("status") == "success":
            return "SUCCESS", title or "Ilan yukari tasindi"
        if any(w in title.lower() for w in ("dakika", "saat", "saniye", "bekle", "süre", "sure")):
            return "TOO_EARLY", title
        if any(w in title.lower() for w in ("giris", "giriş", "oturum", "yetki", "login")):
            return "AUTH", title or "Oturum dustu (cookie yenile)"
        return "UNKNOWN", title or f"Bilinmeyen cevap: {str(data)[:160]}"
    # JSON degil: muhtemelen login HTML'i / blok
    if "giriş" in low or "login" in low or 'name="password"' in low:
        return "AUTH", "Oturum dustu, giris gerek (cookie yenile)"
    return "UNKNOWN", f"Bilinmeyen cevap (HTTP {status}): {text[:120]}"


# title'dan kalan bekleme dakikasini cikar ("26 dakika, 14 saniye" -> 27 dk yukari yuvarla)
_UNITS = (("gün", 1440), ("gun", 1440), ("saat", 60), ("dakika", 1))


def kalan_dakika(title):
    low = (title or "").lower()
    total = 0
    found = False
    for unit, mult in _UNITS:
        m = re.search(r"(\d+)\s*" + unit, low)
        if m:
            total += int(m.group(1)) * mult
            found = True
    sec = re.search(r"(\d+)\s*saniye", low)
    if sec and int(sec.group(1)) > 0:
        total += 1            # saniye varsa 1 dk yukari yuvarla
        found = True
    return total if found else None


# ---- cron-job.org: kendi sonraki tetigini kur (kota dostu, R10 ile ayni mantik) ----
def _cron_api(method, path, api_key, body=None):
    url = "https://api.cron-job.org" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace")


def cron_self_schedule(minutes):
    api_key = _get("WM_CRON_API_KEY", "CRON_API_KEY", default="")
    if not api_key:
        return
    try:
        job_id = _get("WM_CRON_JOB_ID", "CRON_JOB_ID", default="")
        if not job_id:
            _, raw = _cron_api("GET", "/jobs", api_key)
            jobs = json.loads(raw).get("jobs", [])
            cand = [j for j in jobs if "wmaraci-up" in (j.get("url") or "").lower()]
            if not cand:
                logla("cron: wmaraci-up isini bulamadim, planlama atlandi.")
                return
            job_id = cand[0].get("jobId")
        target = (datetime.now(TR) + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
        sched = {
            "timezone": "Europe/Istanbul",
            "hours": [target.hour], "mdays": [target.day],
            "minutes": [target.minute], "months": [target.month], "wdays": [-1],
            "expiresAt": int((target + timedelta(minutes=3)).strftime("%Y%m%d%H%M%S")),
        }
        st, raw = _cron_api("PATCH", f"/jobs/{job_id}", api_key,
                            {"job": {"enabled": True, "schedule": sched}})
        if st in (200, 204):
            logla(f"cron: sonraki tetik {target.strftime('%H:%M')} (job #{job_id}) ayarlandi.")
        else:
            logla(f"cron: beklenmedik yanit HTTP {st}: {raw[:200]}")
    except Exception as e:
        logla(f"cron: planlama hatasi (yedek cron devrede): {e}")


def main():
    if not THREAD_IDS:
        logla("THREAD_IDS bos - tasinacak ilan yok.")
        if TEST:
            telegram(tg_msg("⚠️", "-", "THREAD_IDS bos, tasinacak ilan yok."))
        return
    if not COOKIE:
        logla("WMARACI_COOKIE bos - oturum yok.")
        if TEST:
            telegram(tg_msg("🔑", "-", "WMARACI_COOKIE bos, oturum yok."))
        return

    st = state_oku()
    ikon = {"SUCCESS": "✅", "TOO_EARLY": "⏳", "CLOUDFLARE": "🚫",
            "AUTH": "🔑", "UNKNOWN": "⚠️", "ERROR": "❌"}
    next_targets = []   # bir sonraki tetik icin her ilanin "kac dk sonra" degeri
    did_bump = False     # bu calismada gercek bir SUCCESS oldu mu (cron API'yi sadece o zaman cagir)

    for tid in THREAD_IDS:
        # Client-side kapi: son basarili tasimadan beri GATE_MIN (30 dk) gecmediyse ATLA
        # (bos istek atip Cloudflare'i yormamak icin). TEST modunda kapiyi atla.
        last = st.get(tid, {}).get("last_bump")
        if last and not TEST:
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
            except Exception:
                elapsed = 9999
            if elapsed < GATE_MIN:
                remaining = GATE_MIN - elapsed          # 30 dk penceresinin acilmasina kalan
                # KUCUK kalan (tetik pencereden birkac sn/dk once geldi): ATLAMA — o kadar bekle,
                # pencere acilir acilmaz tasi. Bu olmadan 30 dk'lik tetik "29.x/30, atlandi" deyip
                # bir sonraki tetige birakiyor -> ilan fiilen SAATTE BIR tasiniyordu (kanitli hata).
                # Boylece cron-job.org */5 sagana gerekmez; tek 30 dk'lik tetik yeter (kota dostu).
                if remaining <= GATE_WAIT_MAX:
                    wait_s = int(remaining * 60) + 20   # +20 sn: sunucu limitinin guvenli ustu
                    logla(f"#{tid}: pencereye {remaining:.1f} dk var, {wait_s} sn beklenip tasinacak...")
                    time.sleep(wait_s)
                    # bekleme bitti -> asagidaki bump denemesine dus
                else:
                    kalan = max(1, NEXT_OK_MIN - elapsed)
                    logla(f"#{tid}: vakti degil ({elapsed:.1f}/{GATE_MIN} dk). Atlandi.")
                    next_targets.append(kalan)
                    continue

        # Bir ilan icin: gerekirse GECICI hatada RETRY_MIN dk bekleyip MAX_RETRIES kez daha dene.
        # (TEST modunda uyku yok, tek deneme.) Kalici hatalarda (AUTH) uyku yok.
        durum = ozet = None
        for deneme in range(MAX_RETRIES + 1):
            durum, ozet = bump(tid)
            logla(f"#{tid}: {durum} - {ozet}")

            # Oto yeniden-giris: oturum dustuyse (AUTH) telefon+sifre ile taze cookie alip 1 kez tekrar dene.
            # relogin global COOKIE'yi gunceller -> sonraki ilanlar da yeni cookie'yi kullanir.
            if durum == "AUTH" and LOGIN_PHONE and LOGIN_PASSWORD:
                logla(f"#{tid}: oturum dustu, yeniden giris deneniyor...")
                if relogin():
                    durum, ozet = bump(tid)
                    logla(f"#{tid}: yeniden giris sonrasi -> {durum} - {ozet}")
                else:
                    ozet = f"{ozet} (oto yeniden-giris BASARISIZ — telefon/sifre secret'i kontrol et)"

            # Sadece GECICI hatada, deneme hakki kaldiysa ve TEST degilse: 5 dk bekleyip tekrar dene.
            if durum in _TRANSIENT and deneme < MAX_RETRIES and not TEST:
                logla(f"#{tid}: gecici hata, {RETRY_MIN} dk beklenip tekrar denenecek "
                      f"({deneme + 1}/{MAX_RETRIES})...")
                time.sleep(RETRY_MIN * 60)
                continue
            break

        bildir = TEST
        if durum == "SUCCESS" and NOTIFY_SUCCESS: bildir = True
        if durum == "TOO_EARLY" and NOTIFY_TOO_EARLY: bildir = True
        if durum in ("CLOUDFLARE", "AUTH", "UNKNOWN", "ERROR") and NOTIFY_ERROR: bildir = True
        if bildir:
            telegram(tg_msg(ikon.get(durum, "❓"), tid, ozet))

        if durum == "SUCCESS":
            did_bump = True
            st.setdefault(tid, {})["last_bump"] = datetime.now(timezone.utc).isoformat()
            next_targets.append(NEXT_OK_MIN)        # basarili -> 31 dk sonra
        elif durum == "TOO_EARLY":
            km = kalan_dakika(ozet)
            next_targets.append(km if km is not None else NEXT_OK_MIN)
        else:
            next_targets.append(RETRY_MIN)          # hata/oturum/cloudflare -> 2 dk sonra tekrar

    state_yaz(st)

    # NOT (2026-06-29): cron-job.org self-scheduling KALDIRILDI. Sebep: her calismada
    # API'ye yazmak hesabin API kotasini yaktiriyordu (HTTP 429) -> tetik zinciri oluyordu.
    # Yeni mimari: cron-job.org isi (#7942306) SABIT takvimde (her 30 dk) repository_dispatch
    # atar; script API'yi HIC cagirmaz, dolayisiyla kota patlamaz, sabit takvim ezilmez.
    # (cron_self_schedule fonksiyonu duruyor ama artik cagrilmiyor.)
    _ = did_bump  # bilgi amacli; ayri bir islem yok


if __name__ == "__main__":
    main()
