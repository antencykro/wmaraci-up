# Wmaracı Yukarı Taşıma (bulut otomasyonu)

wmaraci.com ilanını **30 dakikada bir otomatik yukarı taşır** ve sonucu Telegram'daki
**"R10 Yukari Tasima"** kanalına **"Wmaracı Yukarı Taşıma"** başlığıyla bildirir.
GitHub Actions'ta çalışır — **bilgisayar kapalı olsa bile** çalışır. (R10 kurulumunun aynısı.)

## Nasıl çalışır
- `cron-job.org` her ~30 dk `repository_dispatch` ile workflow'u tetikler.
- `wmaraci_yukari.py` → `POST /api/forum/moveUpThread {"threadId":...}` ile ilanı taşır.
  - **Taşındı** → Telegram'a ✅
  - **Süre dolmamış** (30 dk limiti) → sessiz atlar (zararsız)
  - **Cloudflare / oturum hatası** → Telegram'a 🚫/🔑 (çerez yenilenmeli), **5 dk** sonra tekrar
- Her başarılı taşımadan sonra script cron-job.org işini **+31 dk sonraya tek sefer** kurar
  (kota dostu); hata olursa **+5 dk** sonraya. GitHub'ın saatlik yedek cron'u zincir koparsa devreye girer.

## Kurulum (R10 ile birebir aynı adımlar)

### 1. Repo
Bu klasörü yeni bir **özel** GitHub repo'suna pushla (örn. `KULLANICI/wmaraci-up`).

### 2. Secret'lar (Settings → Secrets and variables → Actions → **Secrets**)
| Secret | Değer |
|---|---|
| `WMARACI_COOKIE` | wmaraci giriş çerezi (en az `PHPSESSID` + `loginHash`) |
| `TG_BOT_TOKEN` | `8745193088:AAH...` (R10 ile aynı bot) |
| `TG_CHAT_ID` | `-1004309378445` (R10 kanalı; paylaşılıyor) |
| `WM_CRON_API_KEY` | cron-job.org → Settings → API anahtarı |

### 3. Variable (Settings → Secrets and variables → Actions → **Variables**)
| Variable | Değer |
|---|---|
| `WM_CRON_JOB_ID` | cron-job.org'da oluşturduğun "wmaraci cron" işinin Job ID'si |

Hedef ilan(lar) workflow dosyasındaki `WMARACI_THREAD_IDS` içinde (virgülle çoğalt).

### 4. cron-job.org işi
| Alan | Değer |
|---|---|
| URL | `https://api.github.com/repos/KULLANICI/wmaraci-up/dispatches` |
| Method | POST |
| Body | `{"event_type":"wmaraci-up"}` |
| Schedule | `*/30 * * * *` (script sonra tek-seferliğe çevirir) |

**Headers:**
```
Accept: application/vnd.github+json
Authorization: Bearer <fine-grained PAT — Contents: write, repo: wmaraci-up>
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```
İşi oluşturduktan sonra **Job ID**'sini al → `WM_CRON_JOB_ID` variable'ına yaz.

## Elle test
GitHub → Actions → **Wmaraci Yukari Tasima** → **Run workflow** (test açık) → ~1 dk içinde
kanala "Wmaracı Yukarı Taşıma" mesajı düşer.

## Çerez süresi dolarsa
Telegram'a 🚫/🔑 gelirse: tarayıcıda wmaraci'ye girip F12 → Network'ten yeni çerezi al,
`WMARACI_COOKIE` secret'ını güncelle. (Cookie yerine email/şifre login de eklenebilir — captcha yok.)

## Bakım
- cron-job.org'daki **GitHub PAT** süresi dolunca tetik durur → yeni token üret, cron-job.org `Authorization` güncelle.
- **WM_CRON_API_KEY** değişirse secret'ı güncelle.
- Çerez gerçekten ölürse (nadir) → `WMARACI_COOKIE` yenile.
