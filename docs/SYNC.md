# burnmeter Sync — çok-cihaz (Pro) mimarisi

> **Durum:** TASARIM (henüz kod yok). Bu doküman üzerinde anlaşıp fazalı inşa edeceğiz.
> **İş modeli:** open-core. Free = %100 lokal (değişmez). **Pro = cihazlar arası sync** (Obsidian Sync $4/ay modeli).

## 0. Tasarım ilkeleri (pazarlık konusu değil)

1. **Free tier dokunulmaz.** Lokal çekirdek (CLI + dashboard + parser) **sıfır 3rd-party bağımlılık**, %100 lokal kalır. Sync ayrı, opt-in bir modül.
2. **Gizlilik = E2E.** Markanın sözü "veri makineni terk etmez". Pro'da terk eder AMA **uçtan-uca şifreli** — relay yalnızca **ciphertext** görür, kullanım verini ASLA okuyamaz. Passphrase cihazdan çıkmaz. (Obsidian'ın aynısı.)
3. **Ham log gönderme.** ~/.claude + ~/.codex GB'larca (codex tek dosya 7.7GB görüldü). Sync edilen = **küçük hesaplanmış snapshot** (per-device özet rapor JSON, ~KB). UI zaten per-device gösteriyor → sadece besle.
4. **Self-host edilebilir relay.** Kullanıcı isterse kendi relay'ini çalıştırır (gizlilik max). Bizim hosted relay = kolaylık (ücret buradan).

## 1. Ne sync edilir

Her cihaz periyodik olarak (örn 60sn) kendi **device snapshot**'ını üretir:
```
{ device_id, label, source: claude|codex, generated_at,
  totals, forecast(burn/month), current_window, by_project(top), record_count }
```
Bu, `build_report()`'un küçük bir alt-kümesi (~2-10 KB). Ham JSONL ASLA gönderilmez.

## 2. Şifreleme (E2E)

- Kullanıcı bir **sync passphrase** belirler (tüm cihazlarında aynı). Cihazdan çıkmaz.
- Anahtar = `scrypt(passphrase, account_salt)`. Snapshot **AES-GCM** (veya Fernet) ile şifrelenir → relay'e ciphertext gider.
- Relay deşifre edemez (anahtar yok). Diğer cihaz çeker + lokal deşifre eder.
- **Bağımlılık:** `cryptography` (Fernet/AES). YALNIZCA sync modülünde **lazy import** → free çekirdek sıfır-dep kalır. (`pip install burnmeter[sync]` extra'sı.)

## 3. Relay API (self-host edilebilir, stateless-ish blob store)

| Method | Endpoint | Auth | İş |
|---|---|---|---|
| `PUT` | `/v1/snap/{device_id}` | Bearer account_token | ciphertext blob'u sakla (upsert) |
| `GET` | `/v1/snaps` | Bearer account_token | hesabın TÜM cihaz ciphertext'lerini döndür |
| `DELETE` | `/v1/snap/{device_id}` | Bearer account_token | cihazı kaldır |
| `GET` | `/v1/account` | Bearer | plan (free/pro), cihaz limiti, kullanım |

- Depolama: per-account dizin / SQLite (blob + meta). Relay plaintext görmez.
- **Pro gate relay'de:** token → account → `plan==pro` değilse `PUT` reddedilir (free'de sync yok). Cihaz limiti (örn Pro=5 cihaz) burada zorlanır.

## 4. İstemci (burnmeter tarafı)

Yeni CLI: `burnmeter sync`
- `burnmeter sync login` — account_token + passphrase'i lokal config'e yazar (`~/.config/burnmeter/sync.json`, 0600).
- `burnmeter sync push` — bu cihazın snapshot'ını şifreleyip relay'e PUT. (serve içinde arka planda periyodik de çağrılır.)
- `burnmeter sync pull` — relay'den tüm cihaz ciphertext'lerini çek, deşifre et, lokal cache'e yaz.
- `burnmeter sync status` — bağlı cihazlar, son sync, plan.
- Dashboard: çekilen uzak-cihaz snapshot'larını mevcut **per-device kırılıma** enjekte eder (UI hazır). "Cihaz" seçici + birleşik görünüm.

## 5. Billing (kod MVP'sinin DIŞINDA — entegrasyon noktası)

- MVP: account_token statik allowlist / lokal `plan=pro` flag (gerçek ödeme yok, mekanizmayı test için).
- Üretim: **Lemon Squeezy** veya **Gumroad** (indie-dostu, Stripe'tan basit; webhook → account.plan=pro). Stripe = ileri seviye.
- Pricing: ~$4/ay (Obsidian Sync paritesi) — netleşecek.

## 6. Fazlar (inşa sırası)

- **F1 — Transport MVP (lokal test):** relay (self-host) + `burnmeter sync push/pull/status` + E2E şifreleme + stub Pro-gate. Lokal: relay başlat → cihaz A push → cihaz B pull → dashboard'da 2. cihaz görünür. **Demoable, dış bağımlılık yok.**
- **F2 — Dashboard entegrasyonu:** uzak snapshot'ları per-device UI'a bağla + cihaz seçici + "serve" içinde periyodik auto-push.
- **F3 — Hesap + billing:** Lemon Squeezy webhook → plan, account portal, cihaz limiti.
- **F4 — Hosted relay:** bizim sunucumuzda 7/24 relay (Fly.io/Render/VPS) + TLS + rate-limit + yedek. (Self-host hep seçenek kalır.)

## 7. Senin vereceğin 3 karar (build'i şekillendirir)

1. **Relay hosting:** (a) önce self-host edilebilir relay (sen/kullanıcı çalıştırır) → sonra hosted [ÖNERİLEN], yoksa (b) doğrudan hosted'a git (daha çok ön-iş).
2. **Şifreleme bağımlılığı:** Pro modülünde `cryptography` (E2E için) — free çekirdek sıfır-dep kalır, `burnmeter[sync]` extra'sı [ÖNERİLEN]. Onaylıyor musun?
3. **Billing sağlayıcı:** Lemon Squeezy [ÖNERİLEN indie] / Gumroad / Stripe / şimdilik stub-sadece. (F3'e kadar gerekmez.)
