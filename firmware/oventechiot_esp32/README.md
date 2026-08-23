# OventechIOT ESP32 Firmware

ESP32'yi Delta PLC (RS485/Modbus RTU) ile oventechIOT sunucusu arasında köprü olarak çalıştırır.

## Gerekli kütüphaneler (Arduino IDE → Araçlar → Kütüphaneleri Yönet)

| Kütüphane | Yazar |
|---|---|
| WiFiManager | tzapu |
| ModbusMaster | 4-20ma |
| ArduinoJson | bblanchon (v6 ya da v7 — kod ikisiyle de derlenir) |

Kart seçimi: **ESP32 Dev Module** (Araçlar → Kart → ESP32 Arduino).

## Donanım bağlantısı (MAX485 modülü ile)

```
ESP32 GPIO17 (TX2)  ---> MAX485 DI
ESP32 GPIO16 (RX2)  <--- MAX485 RO
ESP32 GPIO4         ---> MAX485 DE + RE (birlikte)
ESP32 3V3, GND      ---> MAX485 VCC, GND
MAX485 A/B          ---> PLC'nin Modbus RTU (RS485) hattı
```

Farklı pin kullanacaksan `.ino` dosyasının başındaki `PIN_RS485_*` sabitlerini değiştir.

`MODBUS_BAUD` ve `MODBUS_SLAVE_ID` sabitlerini PLC'nin RTU port ayarlarıyla eşleştir.

## İlk kurulum (WiFi ayarları koda yazılmıyor)

1. Firmware'i ESP32'ye yükle, açılışta hiçbir WiFi bilgisi yoksa kendi `OventechIOT-Kurulum` adlı ağını yayınlar.
2. Telefon/laptop ile o ağa bağlan — açılan sayfada (captive portal):
   - Fabrikadaki/evdeki WiFi'yi seç, şifresini gir.
   - **Sunucu Adresi**: `https://oventechiot.onrender.com` (varsayılan zaten bu).
   - **Cihaz Kimliği**: oventechIOT'ta "Cihaz Ekle" yaptığında gösterilen kod (cihaz yönetim sayfasında da görünür).
3. Kaydet — ESP32 yeniden başlar, artık o WiFi'ye otomatik bağlanır ve sunucuyla senkron olur.

Ayarları sıfırlamak (yeni WiFi/sunucu girmek) istersen: açılışta **BOOT** butonuna ~5 saniye basılı tut.

## WiFi koparsa ne olur (kendi kendini toparlama)

Cihaz zaten çalışırken (kurulumdan sonra) WiFi kesilirse **kurulum portalına hiç girmez** — kayıtlı bilgilerle arka planda sürekli `WiFi.reconnect()` dener. Ama bu tek başına saatlerce süren kesintilerde bazen yetersiz kalabiliyor (ESP32'nin WiFi yığını uzun süre bağlantısız kalınca "takılıp kalabiliyor", router geri gelse bile bir daha bağlanamıyor) — bu yüzden **WiFi 5 dakikadan uzun süre kesik kalırsa cihaz kendini otomatik olarak yeniden başlatır**; açılışta kayıtlı WiFi bilgileriyle sıfırdan dener. Yani en kötü ihtimalle WiFi geri geldikten sonraki birkaç dakika içinde kendiliğinden toparlanır, elle müdahaleye gerek yok.

**Açılışta (reboot anında) WiFi zaten ulaşılamıyorsa ne olur?** Daha önce kurulmuş bir cihazda (yani cihaz kimliği zaten kayıtlıysa) kurulum portalı **açılmaz** — açılsaydı cihaz "elle ayar bekliyormuş" gibi askıda kalırdı. Bunun yerine kayıtlı WiFi ağı arka planda sürekli denenir (5 dakikayı aşarsa kendini yeniden başlatarak); ağ geri geldiği an otomatik bağlanır. Kurulum portalı SADECE şu iki durumda açılır: (1) cihaz hiç kurulmamışsa (kimlik boş), (2) açılışta BOOT butonuna ~5sn basılı tutulup elle sıfırlama istenmişse.

## Buton/switch'e basınca kısa süreliğine eski değere dönme sorunu (çözüldü, v1.3.0)

Kullanıcı raporu: panelden bir butona/switch'e basınca PLC'de ve panelde önce doğru değer görünüyor, sonra kısa süreliğine eski değere dönüp bekliyor, sonra tekrar doğrusuna geçiyordu. Kök neden: ESP32'nin döngüsü ÖNCE PLC'den okuyup o (henüz yazma uygulanmamış) değerleri sunucuya bildiriyor, SONRA sunucudan gelen yazmayı PLC'ye uyguluyordu — hepsi aynı döngüde. Artık `yazilacaklariOnceUygula()` ile döngü başında ayrı bir uçtan (`/esp32/<kimlik>/yazilacaklar`) yazılacaklar önce çekilip PLC'ye uygulanıyor, SONRA okuma yapılıyor — o döngüde okunan/bildirilen değer artık zaten yeni (yazılmış) değeri yansıtıyor.

## Nasıl çalışır

- Açılışta ve her dakika bir: sunucudan bu cihazın tag listesini çeker (`GET /esp32/<kimlik>/tagler`) — hangi Modbus adresi, hangi veri tipi (bool/int/float/...), okuma mı yazma mı.
- Her 5 saniyede bir: tüm tag'leri PLC'den Modbus ile okur, sunucuya gönderir (`POST /esp32/<kimlik>/xchange`); sunucu cevabında "kullanıcının panelden yazmak istediği" değerler varsa onları PLC'ye Modbus ile yazar.
- **Alarm mantığı firmware'de YOK** — sunucu her gelen değeri kendisi değerlendirir (bkz. `database.alarm_degerlendir`), böylece tarayıcı kapalı olsa bile alarm oluşur/kaydedilir.
- Grafik geçmişi de sunucu tarafında en fazla ~20 saniyede bir satır olacak şekilde otomatik seyreltilir — firmware'in bunu bilmesine gerek yok, istediği sıklıkla gönderebilir.

## Durum LED'leri

gemba-iot-gateway ile aynı kart düzeni/pin numaraları kullanılıyor — kırmızı (pin 27, Modbus) bu firmware'e hiç dahil değil, kendi haliyle çalışmaya devam ediyor.

| LED | Pin | Nefes alıyorsa | Hızlı yanıp sönüyorsa |
|---|---|---|---|
| 🔵 Mavi | 18 | WiFi bağlı | WiFi bağlı değil / bağlanmaya çalışıyor |
| 🟡 Sarı | 19 | Sunucuyla son senkron başarılı | Sunucuya ulaşılamıyor / cevap hatalı |
| 🟢 Yeşil | 21 | Genel sistem sağlıklı (WiFi + sunucu ikisi de OK) | Söner (WiFi ya da sunucu sorunlu) |
| 🔴 Kırmızı | 27 | — bu firmware'de yönetilmiyor, dokunulmuyor | — |

⚠️ **Açılışta ("Connecting to SAVED AP..." satırı Seri Port'ta görünürken) LED'ler NEFES ALMAZ/YANIP SÖNMEZ — SABİT yanar.** Sebep: nefes/blink efekti `loop()` içinde çalışıyor, ama WiFi'ye bağlanma denemesi (`wm.autoConnect()`) `setup()` içinde ve BLOKLAYICI — bağlantı kurulana (ya da zaman aşımına uğrayana) kadar `loop()` hiç başlamaz. Bu yüzden bu bekleme süresinde: 🔵 Mavi LED sabit yanarak "bağlanmaya çalışıyorum" der; kayıtlı ağ bulunamayıp kurulum portalı (`OventechIOT-Kurulum` ağı) açılırsa 🟡 Sarı LED de sabit yanıp "ayar portalı açık, kurulum bekleniyor" durumunu ekler. Cihaz WiFi'ye bağlanıp `loop()` başladığında LED'ler normal nefes/yanıp-sönme davranışına döner.

## Uzaktan güncelleme (OTA)

oventechIOT'ta cihazın yönetim sayfasında (cihaz_detay) bir "📡 Firmware" kartı var — oradan `.bin` dosyası yüklersin, hedef olarak "Bu Cihaz" ya da "Tüm Cihazlar" seçersin. ESP32 her 10 dakikada bir sunucuya sorar (`GET /esp32/<kimlik>/firmware/kontrol`), yeni sürüm varsa indirip kendini flaslar ve yeniden başlar — kabloya bağlamana gerek yok.

**Yeni bir sürüm derleyip yüklerken** `.ino` dosyasının başındaki `FIRMWARE_VERSION` sabitini artırmayı unutma (örn. `"1.0.0"` → `"1.1.0"`) — sürüm karşılaştırması bu string'e göre yapılıyor, artırmazsan ESP32 "zaten güncelim" diyip indirmez.

## Veri tipi ↔ Modbus eşlemesi

| veri_tipi | Modbus | Register sayısı |
|---|---|---|
| bool, erişim=okuma+yazma/sadece-yazma | Coil (fonksiyon 01/05) | 1 bit |
| bool, erişim=sadece-okuma | Discrete Input (fonksiyon 02) | 1 bit |
| int, sint, uint, usint, byte, word | Holding Register (03/06) | 1×16bit |
| dint, udint, dword | Holding Register (03/16) | 2×16bit (big-endian) |
| float | Holding Register (03/16), IEEE754 | 2×16bit (big-endian) |
| string | — desteklenmiyor | (projene özel; gerekirse ayrıca konuşuruz) |

`tagler.modbus_adres` alanındaki sayı doğrudan register/coil adresi olarak kullanılır. Float/dint/udint/dword için firmware **DÜŞÜK word'ü `modbus_adres`'te, YÜKSEK word'ü `modbus_adres+1`'de** okur/yazar.

⚠️ **Delta PLC'lerde gerçek deneyimle görüldü:** WPLSoft/ISPSoft'ta bir float değişkeni izlerken görünen "tek" register numarası bazen çiftin **yüksek** (ikinci) yarısı oluyor — bu durumda `modbus_adres`'e o görünen numaradan **1 eksiğini** gir (örn. izlemede D4097 görünüyorsa tag'e 4096 yaz). Emin değilsen: yanlış adresle deneyip seri monitördeki `[ham] reg0=... reg1=...` çıktısına bak — biri sürekli `0x0000` geliyorsa muhtemelen bir eksiğini denemen gerekiyor.

⚠️ **Fiziksel PLC girişleri (X) — bool + sadece-okuma zorunlu (v1.4.0):** Delta'nın X (giriş rölesi) noktaları Modbus'ta coil değil, **discrete input** bölgesindedir (fonksiyon 02) — coil (fonksiyon 01, Y çıkışı/M dahili röle için doğru) ile okumaya çalışırsan PLC isteği reddedip "Modbus hatası" verir. Firmware bunu **tag'in erişim türüne göre otomatik** ayırt eder: `erişim=sadece-okuma` olan bool tag'ler discrete input (02) ile, `yazma`/`okuma+yazma` olanlar (Y/M gibi yazılabilir bitler) coil (01) ile okunur — bu yüzden bir X girişini tag'e eklerken **erişimi mutlaka "Sadece okuma" seç**.

Adres için: X numaraları OKTAL'dir (000-007, sonra 010'a atlar, 008/009 yok). Delta'nın kendi "MODBUS ADRESLERİ" tablosundaki sayının (örn. X21 için 11042) `modbus_adres`'e **doğrudan mı** yoksa Modicon geleneğindeki gibi **10001 çıkarılarak mı** girilmesi gerektiği modele/dokümana göre değişebilir, burada kesin bir formül veremiyoruz — deneyerek bul: önce tablodaki sayıyı olduğu gibi dene, olmazsa 10001 (ya da 1) eksiğini dene; seri monitördeki `[oku] ... -> (OKUNAMADI / Modbus hatasi)` satırı düzelip gerçek 0/1 değeri görünmeye başladığında doğru adresi bulmuşsundur.

<!-- yedekleme boru hattı testi 2026-08-22T18:13:29Z -->
