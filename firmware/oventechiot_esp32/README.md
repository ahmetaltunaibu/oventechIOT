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

## Nasıl çalışır

- Açılışta ve her dakika bir: sunucudan bu cihazın tag listesini çeker (`GET /esp32/<kimlik>/tagler`) — hangi Modbus adresi, hangi veri tipi (bool/int/float/...), okuma mı yazma mı.
- Her 5 saniyede bir: tüm tag'leri PLC'den Modbus ile okur, sunucuya gönderir (`POST /esp32/<kimlik>/xchange`); sunucu cevabında "kullanıcının panelden yazmak istediği" değerler varsa onları PLC'ye Modbus ile yazar.
- **Alarm mantığı firmware'de YOK** — sunucu her gelen değeri kendisi değerlendirir (bkz. `database.alarm_degerlendir`), böylece tarayıcı kapalı olsa bile alarm oluşur/kaydedilir.
- Grafik geçmişi de sunucu tarafında en fazla ~20 saniyede bir satır olacak şekilde otomatik seyreltilir — firmware'in bunu bilmesine gerek yok, istediği sıklıkla gönderebilir.

## Uzaktan güncelleme (OTA)

oventechIOT'ta cihazın yönetim sayfasında (cihaz_detay) bir "📡 Firmware" kartı var — oradan `.bin` dosyası yüklersin, hedef olarak "Bu Cihaz" ya da "Tüm Cihazlar" seçersin. ESP32 her 10 dakikada bir sunucuya sorar (`GET /esp32/<kimlik>/firmware/kontrol`), yeni sürüm varsa indirip kendini flaslar ve yeniden başlar — kabloya bağlamana gerek yok.

**Yeni bir sürüm derleyip yüklerken** `.ino` dosyasının başındaki `FIRMWARE_VERSION` sabitini artırmayı unutma (örn. `"1.0.0"` → `"1.1.0"`) — sürüm karşılaştırması bu string'e göre yapılıyor, artırmazsan ESP32 "zaten güncelim" diyip indirmez.

## Veri tipi ↔ Modbus eşlemesi

| veri_tipi | Modbus | Register sayısı |
|---|---|---|
| bool | Coil (fonksiyon 01/05) | 1 bit |
| int, sint, uint, usint, byte, word | Holding Register (03/06) | 1×16bit |
| dint, udint, dword | Holding Register (03/16) | 2×16bit (big-endian) |
| float | Holding Register (03/16), IEEE754 | 2×16bit (big-endian) |
| string | — desteklenmiyor | (projene özel; gerekirse ayrıca konuşuruz) |

`tagler.modbus_adres` alanındaki sayı doğrudan register/coil adresi olarak kullanılır.
