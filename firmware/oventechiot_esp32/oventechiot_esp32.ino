/*
 * OventechIOT — ESP32 Köprü Firmware'i
 * ======================================
 * Bu ESP32, bir tarafta RS485/Modbus RTU ile Delta PLC'ye, diğer tarafta
 * WiFi üzerinden HTTPS ile oventechIOT sunucusuna bağlanır. Görevi basit:
 *
 *   1) Sunucudan bu cihazın tag listesini çeker (hangi Modbus adresi,
 *      hangi veri tipi, okuma mı yazma mı) — GET /esp32/<kimlik>/tagler
 *   2) Döngüde: PLC'den tüm tag'leri Modbus ile okur, sunucuya gönderir
 *      (POST /esp32/<kimlik>/xchange), sunucudan "kullanıcı bunu yazmak
 *      istedi" diye gelen değerleri PLC'ye Modbus ile yazar.
 *
 * Sunucu tarafı zaten alarm değerlendirmesini KENDİSİ yapıyor (tarayıcı
 * kapalı olsa bile) — bu yüzden firmware'in alarm mantığı bilmesine hiç
 * gerek yok, sadece değerleri düzenli göndermesi yeterli.
 *
 * ---------------------------------------------------------------
 * GEREKLİ KÜTÜPHANELER (Arduino IDE > Araçlar > Kütüphaneleri Yönet):
 *   - WiFiManager      (tzapu/WiFiManager)
 *   - ModbusMaster      (4-20ma/ModbusMaster)
 *   - ArduinoJson       (bblanchon/ArduinoJson, sürüm 6.x)
 *   - (WiFiClientSecure, HTTPClient, Preferences — ESP32 çekirdeğiyle gelir)
 *
 * KART: "ESP32 Dev Module" (Arduino IDE > Araçlar > Kart)
 *
 * ---------------------------------------------------------------
 * DONANIM BAĞLANTISI (RS485 — MAX485 modülü ile):
 *   ESP32 GPIO17 (TX2)  ---> MAX485 DI
 *   ESP32 GPIO16 (RX2)  <--- MAX485 RO
 *   ESP32 GPIO4         ---> MAX485 DE + RE (birlikte, yön kontrolü)
 *   ESP32 3V3/5V, GND   ---> MAX485 VCC, GND
 *   MAX485 A/B          ---> PLC'nin RS485 (Modbus RTU) hattı A/B'sine
 *
 * Pin numaraları farklıysa aşağıdaki PIN_* sabitlerini değiştir.
 *
 * ---------------------------------------------------------------
 * İLK KURULUM (her cihaz için, WiFi bilgisi kod içine YAZILMIYOR):
 *   1) ESP32'yi ilk kez aç (ya da BOOT butonuna 5sn basılı tutup sıfırla).
 *   2) Telefonunla "OventechIOT-Kurulum" adlı WiFi ağına bağlan.
 *   3) Açılan sayfada (captive portal) evindeki/fabrikadaki WiFi'yi seç,
 *      şifresini gir; ayrıca "Sunucu Adresi" ve "Cihaz Kimliği" alanlarını
 *      doldur (Cihaz Kimliği: oventechIOT'ta "Cihaz Ekle" yapınca sana
 *      gösterilen kod — cihaz_detay sayfasında da görünür).
 *   4) Kaydet — ESP32 yeniden başlar, artık o WiFi'ye otomatik bağlanır.
 *   5) Ayarları sıfırlamak istersen: BOOT butonuna açılışta ~5sn basılı tut.
 * ---------------------------------------------------------------
 * UZAKTAN GÜNCELLEME (OTA): oventechIOT'ta cihazın yönetim sayfasına
 * (cihaz_detay) .bin dosyası yüklersin, "Bu Cihaz" ya da "Tüm Cihazlar"ı
 * hedeflersin. ESP32 her OTA_CHECK_INTERVAL_MS'de bir sunucuya sorar,
 * yeni sürüm varsa indirip kendini flaslar ve yeniden başlar. Yeni bir
 * sürüm derleyip yüklerken FIRMWARE_VERSION sabitini de artırmayı unutma
 * — sunucu ile cihazdaki sürüm STRING karşılaştırması ("1.2.0" > "1.1.0"
 * gibi noktalı parçalar halinde) ile yapılıyor.
 * ---------------------------------------------------------------
 */

#define FIRMWARE_VERSION "1.0.0"

#include <WiFi.h>
#include <WiFiManager.h>          // tzapu/WiFiManager
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <Update.h>
#include <ArduinoJson.h>          // bblanchon/ArduinoJson v6
#include <Preferences.h>
#include <ModbusMaster.h>         // 4-20ma/ModbusMaster

// ================== AYARLANABİLİR SABİTLER ==================
#define PIN_RS485_RX     16   // ESP32 RX2  <- MAX485 RO
#define PIN_RS485_TX     17   // ESP32 TX2  -> MAX485 DI
#define PIN_RS485_DE_RE   4   // MAX485 DE+RE (yön kontrolü)
#define PIN_RESET_BUTON   0   // BOOT butonu — açılışta 5sn basılıysa ayarları sıfırlar

// Durum LED'leri — gemba-iot-gateway ile AYNI pin numaraları (aynı kart
// düzeni). Kırmızı (MODBUS_LED_PIN=27) burada KASITLI OLARAK YOK — o LED
// zaten doğru çalışıyor, bu firmware ona hiç dokunmuyor.
#define PIN_LED_WIFI     18   // mavi — WiFi bağlı: nefes alır, değilse hızlı yanıp söner
#define PIN_LED_SUNUCU   19   // sarı — sunucu senkronu: başarılıysa nefes alır, hata varsa hızlı yanıp söner
#define PIN_LED_HEARTBEAT 21  // yeşil — genel sistem sağlığı: WiFi+sunucu hepsi OK'sa nefes alır, biri bozuksa söner

#define MODBUS_BAUD    9600   // Delta PLC ile aynı olmalı (RTU portu ayarı)
#define MODBUS_SLAVE_ID   1   // PLC'nin Modbus slave adresi

#define SENKRON_ARALIK_MS   5000   // Sunucuyla ne sıklıkla senkron olunacak
#define TAG_LISTESI_YENILE_MS  60000UL   // Tag listesi ne sıklıkla tazelensin (tasarımda tag eklenmiş/silinmiş olabilir)
#define MAKS_TAG_SAYISI     40

// WiFiManager captive portal'daki ekstra alanlar için varsayılan/ilk değerler
#define VARSAYILAN_SUNUCU  "https://oventechiot.onrender.com"

// ================== DURUM ==================
Preferences ayarlar;
ModbusMaster modbus;
String sunucuAdresi;
String cihazKimlik;

struct TagTanimi {
  int id;
  char ad[32];
  uint16_t modbusAdres;
  char veriTipi[8];   // "bool","int","uint","dint","udint","word","dword","float","string",...
  char erisim[10];    // "read","write","readwrite"
  bool cift_registerli; // true: 2x16bit (dint/udint/dword/float), false: 1x16bit ya da coil
};

TagTanimi tagListesi[MAKS_TAG_SAYISI];
int tagSayisi = 0;
unsigned long sonSenkron = 0;
unsigned long sonTagYenileme = 0;

// ================== DURUM LED'LERİ ==================
// gemba-iot-gateway/led.cpp ile birebir aynı mantık (nefes alma + yanıp
// sönme, PWM ile, non-blocking millis()) — sadece Türkçe isimler korunarak
// tek dosyaya taşındı.
class Led {
  public:
    Led(uint8_t pin) : _pin(pin), _state(false), _lastTime(0),
                        _breatheZaman(0), _parlaklik(0), _breatheYon(1) {}

    void begin() { ledcAttach(_pin, 5000, 8); }  // 5000 Hz, 8-bit (0-255 parlaklık)

    void on()  { ledcWrite(_pin, 255); _state = true; }
    void off() { _parlaklik = 0; _breatheYon = 1; ledcWrite(_pin, 0); _state = false; }

    void blink(uint32_t interval) {
      if (millis() - _lastTime >= interval) {
        _lastTime = millis();
        _state = !_state;
        ledcWrite(_pin, _state ? 255 : 0);
      }
    }

    void breathe(uint32_t hiz) {
      if (millis() - _breatheZaman >= hiz) {
        _breatheZaman = millis();
        _parlaklik += _breatheYon * 5;
        if (_parlaklik >= 255) { _parlaklik = 255; _breatheYon = -1; }
        if (_parlaklik <= 0)   { _parlaklik = 0;   _breatheYon = 1;  }
        ledcWrite(_pin, _parlaklik);
      }
    }

  private:
    uint8_t  _pin;
    bool     _state;
    uint32_t _lastTime;
    uint32_t _breatheZaman;
    int16_t  _parlaklik;
    int16_t  _breatheYon;
};

Led ledWifi(PIN_LED_WIFI);
Led ledSunucu(PIN_LED_SUNUCU);
Led ledHeartbeat(PIN_LED_HEARTBEAT);
// Kullanıcı raporu: cihaz sunucuda hiç kayıtlı olmasa (404) ya da internet
// tamamen gitse (HTTP -1) bile LED'ler "her şey yolunda" gösteriyordu.
// Kök neden: bu bayrak İYİMSER (true) başlıyordu ve SADECE sunucuIleSenkronOl()
// içindeki HTTP/parse hatalarında false oluyordu — ama tag listesi hiç
// alınamadıysa o fonksiyon en baştaki guard'da hiçbir şey denemeden sessizce
// çıkıyor, bayrağı hiç güncellemiyordu. Artık KÖTÜMSER (false) başlıyor —
// LED'ler ancak GERÇEK bir başarılı senkrondan sonra "iyi" gösterecek.
bool sonSenkronBasariliMi = false;  // ledSunucu/ledHeartbeat'in loop()'ta hangi paterni çizeceğine karar vermek için

// ================== RS485 YÖN KONTROLÜ ==================
// ModbusMaster her okuma/yazmadan önce/sonra bu callback'leri çağırır —
// MAX485'i doğru anda gönderim (DE=HIGH) / dinleme (DE=LOW) moduna alır.
void modbusOncesi() { digitalWrite(PIN_RS485_DE_RE, HIGH); delayMicroseconds(50); }
void modbusSonrasi() { delayMicroseconds(50); digitalWrite(PIN_RS485_DE_RE, LOW); }

// ================== YARDIMCI: veri tipine göre register sayısı ==================
bool tipCiftRegisterli(const String& tip) {
  String t = tip; t.toLowerCase();
  return (t == "dint" || t == "udint" || t == "dword" || t == "float");
}

// ================== WiFiManager: özel alanlar (sunucu + cihaz kimliği) ==================
WiFiManagerParameter *paramSunucu;
WiFiManagerParameter *paramKimlik;

void wifiVeSunucuAyarlariniYukle() {
  ayarlar.begin("oventech", false);
  sunucuAdresi = ayarlar.getString("sunucu", VARSAYILAN_SUNUCU);
  cihazKimlik = ayarlar.getString("kimlik", "");
  ayarlar.end();
}

void wifiVeSunucuAyarlariniKaydet() {
  ayarlar.begin("oventech", false);
  ayarlar.putString("sunucu", sunucuAdresi);
  ayarlar.putString("kimlik", cihazKimlik);
  ayarlar.end();
}

// oventechIOT web arayüzüyle aynı görsel dil (koyu lacivert zemin, mavi
// accent, yuvarlak köşeli kartlar) — WiFiManager'ın varsayılan (çok sade)
// temasını setCustomHeadElement() ile ezip kaliteli/profesyonel bir kurulum
// ekranı veriyoruz. WiFiManager'ın kendi HTML'i .wrap/.msg/button/input gibi
// sabit sınıf adları kullanıyor, kütüphaneyi değiştirmeden sadece CSS ile
// yeniden derliyoruz.
const char OVENTECH_PORTAL_CSS[] PROGMEM = R"rawliteral(
<style>
  :root{--bg:#0f1720;--panel:#17222e;--panel2:#1e2d3d;--border:#2a3b4c;--accent:#2e9ed9;--accent-dark:#1f7bb0;--text:#e8eef4;--text-secondary:#8aa0b3;}
  body{background:var(--bg) !important;color:var(--text) !important;font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif !important;margin:0;padding:16px;}
  .wrap{max-width:420px;margin:24px auto;background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:24px 20px;box-shadow:0 8px 28px rgba(0,0,0,0.45);}
  h1,h2,h3{color:var(--text) !important;text-align:center;}
  h1{font-size:20px !important;margin:0 0 4px !important;}
  h3{font-size:13px !important;color:var(--text-secondary) !important;font-weight:400 !important;margin:0 0 18px !important;}
  h1::before{content:"⚙️ ";}
  hr{border:none;border-top:1px solid var(--border);margin:16px 0;}
  a{color:var(--accent) !important;}
  button,input[type='submit'],input[type='button']{background:var(--accent) !important;color:#fff !important;border:none !important;border-radius:8px !important;padding:12px !important;font-size:15px !important;font-weight:600 !important;width:100% !important;box-sizing:border-box !important;cursor:pointer;margin:6px 0 !important;}
  button:hover,input[type='submit']:hover{background:var(--accent-dark) !important;}
  input[type='text'],input[type='password']{background:var(--panel2) !important;color:var(--text) !important;border:1px solid var(--border) !important;border-radius:8px !important;padding:11px !important;font-size:14px !important;box-sizing:border-box !important;width:100% !important;margin:4px 0 10px !important;}
  input:focus{outline:none !important;border-color:var(--accent) !important;}
  label{color:var(--text-secondary) !important;font-size:12px !important;}
  .msg{background:var(--panel2) !important;border:1px solid var(--border) !important;border-radius:8px !important;padding:10px !important;color:var(--text) !important;}
  .msg.S{border-color:#2ecc71 !important;} .msg.D{border-color:#e74c3c !important;}
  .q{background:var(--panel2) !important;border:1px solid var(--border) !important;border-radius:8px !important;margin:6px 0 !important;}
  .q a{color:var(--text) !important;text-decoration:none !important;display:block;padding:10px !important;}
  .q a:hover{background:var(--border) !important;}
  .qi{color:var(--text-secondary) !important;}
  div.wifi-cover-image{display:none !important;}
</style>
)rawliteral";

void kurulumPortaliBaslat(bool zorlaSifirla) {
  WiFiManager wm;
  wm.setCustomHeadElement(OVENTECH_PORTAL_CSS);
  wm.setTitle("OventechIOT Kurulum");
  // Not: setDarkMode() bilerek kullanılmıyor — WiFiManager sürümüne göre
  // bulunmayabilir (derleme hatası riski); yukarıdaki özel CSS zaten
  // !important ile koyu temayı garantiliyor, ayrıca gerek yok.
  if (zorlaSifirla) {
    wm.resetSettings();
  }

  paramSunucu = new WiFiManagerParameter("sunucu", "Sunucu Adresi (https://...)", sunucuAdresi.c_str(), 80);
  paramKimlik = new WiFiManagerParameter("kimlik", "Cihaz Kimligi (oventechIOT'tan)", cihazKimlik.c_str(), 64);
  wm.addParameter(paramSunucu);
  wm.addParameter(paramKimlik);

  wm.setConfigPortalTimeout(300); // 5 dakika içinde ayar girilmezse tekrar dener
  bool baglandi = wm.autoConnect("OventechIOT-Kurulum");

  if (!baglandi) {
    Serial.println("WiFi baglanamadi, yeniden baslatiliyor...");
    delay(3000);
    ESP.restart();
  }

  // Kullanıcı portal ekranını doldurup kaydettiyse buradaki değerler günceldir.
  sunucuAdresi = String(paramSunucu->getValue());
  cihazKimlik = String(paramKimlik->getValue());
  sunucuAdresi.trim();
  cihazKimlik.trim();
  wifiVeSunucuAyarlariniKaydet();

  Serial.println("WiFi baglandi: " + WiFi.localIP().toString());
  Serial.println("Sunucu: " + sunucuAdresi);
  Serial.println("Cihaz Kimligi: " + cihazKimlik);
}

// ================== SUNUCUDAN TAG LİSTESİNİ ÇEK ==================
bool tagListesiniGetir() {
  if (cihazKimlik.length() == 0) { sonSenkronBasariliMi = false; return false; }

  HTTPClient http;
  String url = sunucuAdresi + "/esp32/" + cihazKimlik + "/tagler";
  http.begin(url);
  int kod = http.GET();
  if (kod != 200) {
    Serial.printf("Tag listesi alinamadi, HTTP %d\n", kod);
    http.end();
    sonSenkronBasariliMi = false;  // sunucuyla gerçek iletişim yok — LED'ler bunu yansıtsın
    return false;
  }

  String govde = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError hata = deserializeJson(doc, govde);
  if (hata) {
    Serial.print("JSON parse hatasi (tagler): "); Serial.println(hata.c_str());
    sonSenkronBasariliMi = false;
    return false;
  }

  JsonArray dizi = doc["tagler"].as<JsonArray>();
  tagSayisi = 0;
  for (JsonObject t : dizi) {
    if (tagSayisi >= MAKS_TAG_SAYISI) break;
    TagTanimi &hedef = tagListesi[tagSayisi];
    hedef.id = t["id"] | 0;
    strlcpy(hedef.ad, (t["ad"] | ""), sizeof(hedef.ad));
    // ArduinoJson v7'de "değişken | \"literal\"" ifadesi zincirlenebilir bir
    // JsonVariant değil doğrudan const char* döndürüyor — bu yüzden önce
    // JsonVariantConst olarak alıp öyle .as<String>() çağırıyoruz.
    {
      JsonVariantConst modbusAdresVar = t["modbus_adres"];
      String modbusAdresStr = modbusAdresVar.isNull() ? String("0") : modbusAdresVar.as<String>();
      hedef.modbusAdres = (uint16_t)modbusAdresStr.toInt();
    }
    strlcpy(hedef.veriTipi, (t["veri_tipi"] | "bool"), sizeof(hedef.veriTipi));
    strlcpy(hedef.erisim, (t["erisim"] | "read"), sizeof(hedef.erisim));
    hedef.cift_registerli = tipCiftRegisterli(hedef.veriTipi);
    tagSayisi++;
  }
  Serial.printf("Tag listesi alindi: %d tag\n", tagSayisi);
  return true;
}

// ModbusMaster hata kodunu okunabilir metne çevirir — "Modbus hatası var
// mı, varsa hangisi" diye seri porttan kolayca görebilmek için.
const char* modbusHataMetni(uint8_t kod) {
  switch (kod) {
    case ModbusMaster::ku8MBSuccess: return "OK";
    case ModbusMaster::ku8MBIllegalFunction: return "Gecersiz fonksiyon";
    case ModbusMaster::ku8MBIllegalDataAddress: return "Gecersiz adres (register PLC'de yok)";
    case ModbusMaster::ku8MBIllegalDataValue: return "Gecersiz veri degeri";
    case ModbusMaster::ku8MBSlaveDeviceFailure: return "PLC (slave) hatasi";
    case ModbusMaster::ku8MBInvalidSlaveID: return "Yanlis Slave ID (MODBUS_SLAVE_ID kontrol et)";
    case ModbusMaster::ku8MBInvalidFunction: return "Gecersiz fonksiyon kodu";
    case ModbusMaster::ku8MBResponseTimedOut: return "Zaman asimi (kablo/baudrate/A-B kontrol et)";
    case ModbusMaster::ku8MBInvalidCRC: return "CRC hatasi (baudrate/parity/stop-bit uyusmuyor olabilir)";
    default: return "Bilinmeyen hata";
  }
}

// ================== MODBUS: TEK BİR TAG'İ PLC'DEN OKU ==================
// Sonucu string olarak döner ("1"/"0" bool için, sayı float/int için).
// Okuma başarısızsa boş string döner (o tag bu turda gönderilmez) — hata
// kodu ayrıca seri porta yazdırılır.
String tagOku(const TagTanimi &tag) {
  String tip = String(tag.veriTipi); tip.toLowerCase();

  if (tip == "bool") {
    uint8_t sonuc = modbus.readCoils(tag.modbusAdres, 1);
    if (sonuc != modbus.ku8MBSuccess) { Serial.printf("    -> Modbus hata: %s\n", modbusHataMetni(sonuc)); return ""; }
    return (modbus.getResponseBuffer(0) & 0x01) ? "1" : "0";
  }

  if (tip == "string") {
    return ""; // string tag'ler Modbus register haritasında proje bazlı değişir — burada desteklenmiyor
  }

  if (tag.cift_registerli) {
    // Kullanıcı raporu: tek seferde 2 register okuyunca (readHoldingRegisters(adres,2))
    // İKİNCİ register her zaman 0x0000 geliyordu (145.5->145.0, 1245.5->1240.00 —
    // ikisinde de sadece düşük word kayboluyordu). Bu, çoklu-register okumada bir
    // sorun olduğuna işaret ediyor — bu yüzden iki register'ı AYRI AYRI, tek tek
    // okuyoruz; daha güvenilir ve sorunu da kesin teşhis ediyor.
    uint8_t sonuc0 = modbus.readHoldingRegisters(tag.modbusAdres, 1);
    if (sonuc0 != modbus.ku8MBSuccess) { Serial.printf("    -> Modbus hata (reg0, adres=%u): %s\n", tag.modbusAdres, modbusHataMetni(sonuc0)); return ""; }
    uint16_t reg0 = modbus.getResponseBuffer(0);

    uint8_t sonuc1 = modbus.readHoldingRegisters(tag.modbusAdres + 1, 1);
    if (sonuc1 != modbus.ku8MBSuccess) { Serial.printf("    -> Modbus hata (reg1, adres=%u): %s\n", tag.modbusAdres + 1, modbusHataMetni(sonuc1)); return ""; }
    uint16_t reg1 = modbus.getResponseBuffer(0);

    // Debug: ham register değerlerini hex olarak yazdır.
    Serial.printf("    [ham] reg0(adr=%u)=0x%04X reg1(adr=%u)=0x%04X\n",
      tag.modbusAdres, reg0, tag.modbusAdres + 1, reg1);

    // GEÇİCİ TEŞHİS: adres+1'de (4098) hep 0x0000 çıkıyor — düşük word
    // farklı bir adreste olabilir (örn. adres-1). Etraftaki 5 adresi tek
    // seferde tarayıp hangisinin gerçekten değişen/anlamlı bir değer
    // taşıdığını görelim; bu blok sorun çözülünce kaldırılabilir.
    Serial.print("    [tarama] ");
    for (int off = -2; off <= 2; off++) {
      uint16_t komsuAdres = (uint16_t)(tag.modbusAdres + off);
      uint8_t s = modbus.readHoldingRegisters(komsuAdres, 1);
      if (s == modbus.ku8MBSuccess) {
        Serial.printf("adr%+d(%u)=0x%04X  ", off, komsuAdres, modbus.getResponseBuffer(0));
      } else {
        Serial.printf("adr%+d(%u)=HATA  ", off, komsuAdres);
      }
    }
    Serial.println();

    // gemba-iot-gateway/modbus_handler.cpp'deki (bu PLC ailesinde ÇALIŞAN)
    // kural: DÜŞÜK word ÖNCE (adres), YÜKSEK word SONRA (adres+1) — benim
    // ilk varsayımım (yüksek önce) tersiymiş, düzeltildi.
    uint32_t ham = ((uint32_t)reg1 << 16) | reg0;
    if (tip == "float") {
      float f;
      memcpy(&f, &ham, sizeof(f));
      return String(f, 2);
    }
    // dint (işaretli) / udint / dword (işaretsiz)
    if (tip == "dint") return String((int32_t)ham);
    return String(ham);
  }

  // Tek register: int/uint/word/byte/sint/usint
  uint8_t sonuc = modbus.readHoldingRegisters(tag.modbusAdres, 1);
  if (sonuc != modbus.ku8MBSuccess) { Serial.printf("    -> Modbus hata: %s\n", modbusHataMetni(sonuc)); return ""; }
  uint16_t deger = modbus.getResponseBuffer(0);
  if (tip == "int" || tip == "sint") return String((int16_t)deger);
  return String(deger);
}

// ================== MODBUS: TEK BİR TAG'E PLC'YE YAZ ==================
bool tagYaz(const TagTanimi &tag, const String &degerStr) {
  String tip = String(tag.veriTipi); tip.toLowerCase();

  if (tip == "bool") {
    uint8_t sonuc = modbus.writeSingleCoil(tag.modbusAdres, degerStr.toInt() != 0);
    return sonuc == modbus.ku8MBSuccess;
  }

  if (tag.cift_registerli) {
    uint32_t ham;
    if (tip == "float") {
      float f = degerStr.toFloat();
      memcpy(&ham, &f, sizeof(ham));
    } else {
      ham = (uint32_t)degerStr.toInt();
    }
    // Okumada olduğu gibi (bkz. tagOku) AYRI AYRI yazıyoruz — ve gemba'daki
    // gibi DÜŞÜK word önce (adres), YÜKSEK word sonra (adres+1).
    modbus.setTransmitBuffer(0, (uint16_t)(ham & 0xFFFF));  // düşük word
    uint8_t sonuc0 = modbus.writeMultipleRegisters(tag.modbusAdres, 1);
    if (sonuc0 != modbus.ku8MBSuccess) return false;
    modbus.setTransmitBuffer(0, (uint16_t)(ham >> 16));     // yüksek word
    uint8_t sonuc1 = modbus.writeMultipleRegisters(tag.modbusAdres + 1, 1);
    return sonuc1 == modbus.ku8MBSuccess;
  }

  modbus.setTransmitBuffer(0, (uint16_t)degerStr.toInt());
  uint8_t sonuc = modbus.writeMultipleRegisters(tag.modbusAdres, 1);
  return sonuc == modbus.ku8MBSuccess;
}

// ================== SUNUCUYLA SENKRON (xchange) ==================
void sunucuIleSenkronOl() {
  if (cihazKimlik.length() == 0 || tagSayisi == 0) {
    sonSenkronBasariliMi = false;  // tag listesi yoksa gerçek bir senkron da yok — LED bunu "iyi" saymasın
    return;
  }

  // 1) PLC'den oku
  JsonDocument gonderilecek;
  JsonObject degerler = gonderilecek["degerler"].to<JsonObject>();
  int okunanSayisi = 0;
  for (int i = 0; i < tagSayisi; i++) {
    if (String(tagListesi[i].erisim) == "write") continue; // sadece-yazma tag'i okumaya gerek yok
    String v = tagOku(tagListesi[i]);
    // Debug: her tag'in Modbus'tan okunan HAM değerini seri porta yazdır —
    // "deger 0 geliyor ama Modbus hatası da yok" gibi durumları görebilmek için.
    Serial.printf("  [oku] %-20s adres=%u tip=%-6s -> %s\n",
      tagListesi[i].ad, tagListesi[i].modbusAdres, tagListesi[i].veriTipi,
      v.length() > 0 ? v.c_str() : "(OKUNAMADI / Modbus hatasi)");
    if (v.length() > 0) {
      degerler[String(tagListesi[i].id)] = v;
      okunanSayisi++;
    }
  }

  String govdeStr;
  serializeJson(gonderilecek, govdeStr);

  // 2) Sunucuya gönder, cevapta "yazilacaklar" var mı bak
  HTTPClient http;
  String url = sunucuAdresi + "/esp32/" + cihazKimlik + "/xchange";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  int kod = http.POST(govdeStr);

  if (kod != 200) {
    Serial.printf("xchange basarisiz, HTTP %d\n", kod);
    http.end();
    sonSenkronBasariliMi = false;   // sarı/yeşil LED hata paternine geçer
    return;
  }

  String cevapStr = http.getString();
  http.end();

  JsonDocument cevap;
  if (deserializeJson(cevap, cevapStr)) {
    Serial.println("xchange cevabi parse edilemedi");
    sonSenkronBasariliMi = false;
    return;
  }
  sonSenkronBasariliMi = true;

  // 3) Kullanıcının panelden yazdığı değerleri PLC'ye yaz
  JsonObject yazilacaklar = cevap["yazilacaklar"].as<JsonObject>();
  int yazilanSayisi = 0;
  for (JsonPair kv : yazilacaklar) {
    int tagId = String(kv.key().c_str()).toInt();
    String deger = kv.value().as<String>();
    for (int i = 0; i < tagSayisi; i++) {
      if (tagListesi[i].id == tagId) {
        if (tagYaz(tagListesi[i], deger)) yazilanSayisi++;
        break;
      }
    }
  }

  Serial.printf("Senkron OK — okunan:%d yazilan:%d\n", okunanSayisi, yazilanSayisi);
}

// ================== UZAKTAN GÜNCELLEME (OTA) ==================
// gemba-iot-gateway'deki çalışan sistemle aynı mantık: periyodik kontrol
// et, güncelleme varsa Update.h ile parça parça flash'a yaz, bitince
// yeniden başlat. Sunucuya "başarılı" bildirimi de gönderiyoruz ki
// cihaz_detay sayfasındaki geçmişte görünsün.
#define OTA_CHECK_INTERVAL_MS  (10UL * 60UL * 1000UL)   // 10 dakikada bir kontrol
unsigned long sonOtaKontrol = 0;

bool otaGuncellemeUygula(const String &firmwareUrl, int firmwareId, const String &firmwareFilename) {
  Serial.println("OTA: guncelleme indiriliyor...");

  WiFiClientSecure client;
  client.setInsecure(); // sunucu sertifikasi Render tarafinda yonetiliyor
  HTTPClient http;
  if (!http.begin(client, firmwareUrl)) {
    Serial.println("OTA: HTTP baslatilamadi");
    return false;
  }

  int kod = http.GET();
  if (kod != HTTP_CODE_OK) {
    Serial.printf("OTA: indirme basarisiz, HTTP %d\n", kod);
    http.end();
    return false;
  }

  size_t boyut = http.getSize();
  if (boyut <= 0) {
    Serial.println("OTA: dosya boyutu bilinmiyor, iptal");
    http.end();
    return false;
  }

  if (!Update.begin(boyut, U_FLASH)) {
    Serial.println("OTA: Update.begin hatasi: " + String(Update.getError()));
    http.end();
    return false;
  }

  WiFiClient *stream = http.getStreamPtr();
  uint8_t buf[1024];
  size_t toplamOkunan = 0;
  while (http.connected() && toplamOkunan < boyut) {
    size_t hazir = stream->available();
    if (hazir) {
      size_t okunacak = min(hazir, sizeof(buf));
      size_t n = stream->readBytes(buf, okunacak);
      if (Update.write(buf, n) != n) {
        Serial.println("OTA: flash yazma hatasi");
        http.end();
        Update.end(false);
        return false;
      }
      toplamOkunan += n;
    }
    delay(1);
  }
  http.end();

  if (!Update.end(true)) {
    Serial.println("OTA: finalizasyon hatasi: " + String(Update.getError()));
    return false;
  }

  Serial.println("OTA: flash tamamlandi");

  // Sunucuya "basarili" bildirimi gonder (restart oncesi — restart sonrasi
  // ayrica denenmez, tek seferlik best-effort bildirim yeterli).
  HTTPClient bildirimHttp;
  String bildirimUrl = sunucuAdresi + "/esp32/" + cihazKimlik + "/firmware/basarili";
  bildirimHttp.begin(bildirimUrl);
  bildirimHttp.addHeader("Content-Type", "application/json");
  JsonDocument bildirim;
  bildirim["firmware_id"] = firmwareId;
  bildirim["firmware_filename"] = firmwareFilename;
  String bildirimStr;
  serializeJson(bildirim, bildirimStr);
  bildirimHttp.POST(bildirimStr);
  bildirimHttp.end();

  return true;
}

void otaKontrolEt() {
  if (cihazKimlik.length() == 0) return;

  HTTPClient http;
  String url = sunucuAdresi + "/esp32/" + cihazKimlik + "/firmware/kontrol?version=" FIRMWARE_VERSION;
  http.begin(url);
  int kod = http.GET();
  if (kod != 200) {
    Serial.printf("OTA kontrol basarisiz, HTTP %d\n", kod);
    http.end();
    return;
  }
  String govde = http.getString();
  http.end();

  JsonDocument doc;
  if (deserializeJson(doc, govde)) {
    Serial.println("OTA kontrol cevabi parse edilemedi");
    return;
  }

  bool guncellemeVar = doc["update_available"] | false;
  if (!guncellemeVar) {
    Serial.println("OTA: firmware guncel");
    return;
  }

  String firmwareUrl = doc["firmware_url"] | "";
  String firmwareFilename = doc["firmware_filename"] | "";
  int firmwareId = doc["firmware_id"] | 0;
  String yeniSurum = doc["latest_version"] | "";

  Serial.println("OTA: yeni surum bulundu: v" + yeniSurum);

  if (otaGuncellemeUygula(firmwareUrl, firmwareId, firmwareFilename)) {
    Serial.println("OTA: basarili, yeniden baslatiliyor...");
    delay(1500);
    ESP.restart();
  } else {
    Serial.println("OTA: basarisiz, bir sonraki kontrolde tekrar denenecek");
  }
}

// ================== SETUP / LOOP ==================
void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(PIN_RS485_DE_RE, OUTPUT);
  digitalWrite(PIN_RS485_DE_RE, LOW);
  pinMode(PIN_RESET_BUTON, INPUT_PULLUP);

  ledWifi.begin();
  ledSunucu.begin();
  ledHeartbeat.begin();

  // Modbus RTU — Serial2 üzerinden (RX2=16, TX2=17)
  Serial2.begin(MODBUS_BAUD, SERIAL_8N1, PIN_RS485_RX, PIN_RS485_TX);
  modbus.begin(MODBUS_SLAVE_ID, Serial2);
  modbus.preTransmission(modbusOncesi);
  modbus.postTransmission(modbusSonrasi);

  wifiVeSunucuAyarlariniYukle();

  // Açılışta BOOT butonuna ~5sn basılı tutulursa WiFi/sunucu ayarlarını sıfırla.
  bool sifirlaIstendi = false;
  if (digitalRead(PIN_RESET_BUTON) == LOW) {
    Serial.println("BOOT basili — sifirlama icin 5sn bekleniyor...");
    unsigned long basStart = millis();
    while (digitalRead(PIN_RESET_BUTON) == LOW && millis() - basStart < 5000) delay(50);
    if (millis() - basStart >= 5000) sifirlaIstendi = true;
  }

  kurulumPortaliBaslat(sifirlaIstendi);
  tagListesiniGetir();
  sonTagYenileme = millis();
}

void loop() {
  bool wifiBagliMi = (WiFi.status() == WL_CONNECTED);

  // Durum LED'leri — WiFi bağlı değilken de çalışsın diye erken dönüşten
  // ÖNCE güncelleniyor. Kırmızı (Modbus, pin 27) buraya hiç dahil değil.
  if (wifiBagliMi) ledWifi.breathe(25); else ledWifi.blink(100);
  if (wifiBagliMi && sonSenkronBasariliMi) ledSunucu.breathe(25); else ledSunucu.blink(100);
  if (wifiBagliMi && sonSenkronBasariliMi) ledHeartbeat.breathe(25); else ledHeartbeat.off();

  if (!wifiBagliMi) {
    Serial.println("WiFi baglantisi koptu, yeniden baglaniliyor...");
    WiFi.reconnect();
    delay(2000);
    return;
  }

  unsigned long simdi = millis();

  // Tag listesini periyodik tazele (tasarımda tag eklenmiş/silinmiş olabilir)
  if (simdi - sonTagYenileme >= TAG_LISTESI_YENILE_MS) {
    tagListesiniGetir();
    sonTagYenileme = simdi;
  }

  // Ana senkron döngüsü — sıcaklık gibi veriler burada sürekli gönderiliyor,
  // alarm değerlendirmesi sunucuda otomatik yapılıyor (tarayıcı kapalı olsa bile).
  if (simdi - sonSenkron >= SENKRON_ARALIK_MS) {
    sunucuIleSenkronOl();
    sonSenkron = simdi;
  }

  // Uzaktan güncelleme (OTA) kontrolü — 10 dakikada bir.
  if (simdi - sonOtaKontrol >= OTA_CHECK_INTERVAL_MS) {
    otaKontrolEt();
    sonOtaKontrol = simdi;
  }
}
