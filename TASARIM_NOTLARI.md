# OventechIOT — Tasarım Notları (istişare aşaması, henüz kod yok)

## Genel amaç
Oventech'in (kurutma fırını, kumlama, yaş boya fırını, toz boya fırını panoları)
Delta PLC + Delta HMI ile çalışan makinelerini, müşterilerin uzaktan (ağırlıklı
telefondan) izleyip kontrol edebileceği, çoklu müşterili (multi-tenant) bir
sunucu ürünü.

Gemba ile aynı temel iletişim mantığı (ESP32 köprü cihazı ↔ sunucu, periyodik
durum gönder + komut al) kullanılacak ama **tamamen ayrı bir proje/repo**,
gemba ile hiçbir paylaşımlı altyapı (sunucu, veritabanı) yok.

## Çoklu müşteri (multi-tenant) yapısı
- **Tek sunucu, çoklu müşteri.**
- **Proje** = bir müşterinin çalışma alanı. Kullanıcı adı/şifre ile giriş.
  Bir projeye birden fazla kullanıcı tanımlanabilir.
- Bir projeye birden fazla **cihaz** bağlanabilir (aynı müşterinin birden
  fazla makinesi/fırını olabilir). Kullanıcı giriş yapınca kendi projesindeki
  cihazları görür, cihaz seçince o cihaza ait sayfalar/elementler açılır.
- Müşteriler birbirinin verisini/cihazını asla göremez (tam izolasyon).

## Sayfa/element sistemi (SCADA-vari editör)
- Her cihaz için **kaydedilebilir, özelleştirilebilir sayfalar** olacak.
- Element paleti: buton, label, checkbox, combobox, input box, textbox,
  grafik (chart) — ve zamanla genişleyebilir.
- Her elementin **özellikleri düzenlenebilir olmalı**: yapı, renk, boyut,
  bağlı olduğu veri etiketi (tag) vs. — tam olarak neyin düzenlenebilir
  olacağı referans SCADA dosyası incelenince netleşecek.
- Her element bir **tag/register**'a bağlanır (Modbus üzerinden PLC/HMI
  değeriyle eşleşir) — canlı veri gösterir ya da komut gönderir.

**Mimari karar (öneri, henüz onaylanmadı)**: tam piksel-hassasiyetli sürükle-
bırak yerine önce element kütüphanesi + veri bağlama motorunu sağlam kurup,
sürükle-bırak'ı üstüne bir kolaylık katmanı olarak eklemek. Referans SCADA
dosyasına bakınca bu karar netleşecek.

## Hiyerarşi (taslak)
```
Proje (müşteri, kullanıcı adı/şifre — çoklu kullanıcı olabilir)
  └─ Cihaz(lar) (ESP32 köprü — Delta PLC + HMI, Modbus)
        └─ Sayfa(lar) (cihaza özel, kaydedilmiş element düzeni)
              └─ Elementler (buton/label/checkbox/combobox/input/grafik)
                    └─ Tag/register bağlantısı
```

## İstenen özellikler (ilk beyin fırtınası)
- Sıcaklık
- Çalışıyor / duruyor / arızada durumu
- Çalışma/durma süresi bilgisi
- Telefondan açma/kapatma (uzaktan kontrol)
- Gerçek geri bildirim (komut gönderildi diye değil, makine GERÇEKTEN
  öyle mi diye doğrulama — gemba'daki röle geri bildirimi gibi)
- Alarmlar (anlık + geçmiş)
- Geçmiş grafik / geçmiş veri
- Belki adetler/sayaçlar

## Platform
- **Mobil uyumluluk kritik öncelik** — PWA (Progressive Web App) olarak
  planlanıyor, telefonda "app gibi" çalışsın.
- İleride sistem tutarsa gerçek bir mobil app'e (native) geçiş düşünülüyor.

## Referans: `C:\Users\ahmet.altun\OneDrive\github\scada` (mevcut masaüstü SCADA editörü)

Bu, PySide6 (Qt) ile yazılmış, oldukça olgun/kapsamlı bir masaüstü SCADA
tasarım aracı. OventechIOT'un web/PWA versiyonu için mimari referans olarak
kullanılacak. Öğrendiklerim:

### Element sistemi
- `elements/base_element.py`: tüm elementlerin türediği taban sınıf.
  - `ELEMENT_TYPE`, `DISPLAY_NAME`, `ICON`, `SUPPORTS_STATES` (element birden
    fazla "durum"a — On/Off, 3-4 state — göre görünüm değiştirebiliyor mu)
  - `PROPERTY_GROUPS`: Properties panelinde o elemente ÖZEL alan grupları
    (şema listesi: etiket, prop anahtarı, varsayılan) — ortak alanlar
    (Adres/Koordinat/Stil/Erişim) tüm elementlerde otomatik.
  - `render_designer()` / `render_runtime(tag_values, ctx)` — tasarım
    (statik) ve çalışma zamanı (canlı veri) görünümü ayrı.
  - `to_dict()`/`from_dict()` — **JSON'a birebir uygun**, web/DB'ye taşımak
    kolay.
  - `effective_read_address()` — Read Address boşsa Write Address'e düşer
    (tek adres hem yaz hem oku).
  - `is_invisible(tag_values)` — bir tag değerine göre elementi çalışma
    zamanında gizleme.
  - `is_role_allowed(current_role)` — **rol tabanlı erişim taban sınıfta**:
    `ROLES = ["operator", "engineer", "admin"]`, her elemente "Min. Kullanıcı
    Seviyesi" atanabiliyor.
- `elements/registry.py`: dekoratör tabanlı kayıt (`@register`), Toolbox ve
  Runtime elementleri buradan okuyor — yeni element eklemek başka hiçbir
  yeri değiştirmiyor.
- ~25 hazır element türü: buton (4 modu var: Set On/Set Off/Momentary/
  Maintained), step button, multi-state, goto-page, tarih/saat, checkbox,
  numeric display/entry, character display/entry, moving sign, QR/barkod
  (göster+oku), multilang/multiline entry, trend graph + historical trend,
  graphic indicator, label, pipe, alarm table, çizim şekilleri (line/
  rectangle/polygon/curve/arrow).
- `button_element.py` örneği: tek bir sınıf onlarca özelliği kapsıyor —
  metin/font/renk/gradient/border-radius/margin/transparanlık/blink/resim/
  onay penceresi/interlock adresi/hot-key/ignore-log/write-read-address +
  state başına text/color/bg_color/picture/font_size/css override. Custom
  CSS metni varsa Properties'teki karşılığını EZİYOR (genel kural).

### Veri modeli (`core/database.py`, SQLite — proje başına 1 dosya)
`plcs`, `tag_groups`, `tags`, `tag_history`, `alarm_groups`, `alarms`,
`alarm_events`, `page_groups`, `pages`, `users`, `permissions`,
`audit_log`, `scripts`, `project_meta`.

**Web/multi-tenant'a taşıma önerisi**: bu şema neredeyse hiç değiştirmeden
kullanılabilir — masaüstünde "1 proje = 1 SQLite dosyası" mantığı zaten
bizim "1 müşteri = 1 proje" ihtiyacımızla birebir örtüşüyor. Platform
seviyesinde sadece bir "projeler kayıt defteri" (proje adı → hangi DB/
şema, giriş kullanıcıları) tablosu eklenip, geri kalan her şey bu
kanıtlanmış şemayla proje başına izole edilebilir.

### Diğer modüller
- `core/tag_manager.py`: tag okuma/yazma + ölçek dönüşümü (`scale`/
  `unscale` — ham PLC değerini mühendislik birimine çevirme).
- `core/alarm_manager.py`, `core/historian.py`: alarm ve geçmiş veri motoru
  zaten ayrı modüller olarak var.
- `core/auth.py`: 3 seviyeli rol sistemi (`operator/engineer/admin`).
- `core/drivers/`: Modbus (pymodbus) + Siemens (python-snap7) PLC
  sürücüleri — bizim tarafta Delta PLC/Modbus karşılığı olacak.
- `ui/properties_panel.py`, `ui/toolbox.py`, `ui/canvas.py`,
  `ui/runtime_view.py`: tasarım/özellik-paneli/çalışma-zamanı ayrımı zaten
  net bir şekilde kurulmuş.

### Sonuç
Bu referans, OventechIOT'un **element + tag + sayfa + rol modelini**
neredeyse hazır veriyor — asıl iş bunu web/PWA'ya (sürükle-bırak canvas,
JSON tabanlı element/sayfa saklama, çoklu müşteri izolasyonu, ESP32 köprü
üzerinden canlı veri) taşımak. Kapsam masaüstü versiyondaki KADAR geniş
olursa (25 element türü, her biri onlarca özellik) bu tek seferde
yapılacak küçük bir iş DEĞİL — aşamalı gitmek şart.

**Öneri**: v1 için küçük bir alt küme seçelim (örn. buton, label/numeric
display, checkbox, basit grafik/trend, alarm listesi) + her elementte
SADECE en çok kullanılan özellikler (metin, renk, boyut, adres, state
listesi) — geri kalanı (gradient, blink, hot-key, interlock, çoklu dil vb.)
v2/v3'e bırakalım.

## Açık sorular / netleşmeyi bekleyenler
1. Referans SCADA dosyası incelenip element/özellik-paneli tasarımı ona göre
   uyarlanacak.
2. İlk element seti: hepsi baştan mı, yoksa küçük bir setle mi başlanacak.
3. Alarmlar + geçmiş veri v1'de mi, v2'de mi.
4. Donanım: ESP32 tabanlı köprü, gemba-iot-gateway'deki Modbus/HMI okuma
   mantığına benzer olacak (henüz teyit edilmedi).

---
*Bu dosya sadece istişare/tasarım notlarıdır, henüz hiç kod yazılmadı.*
