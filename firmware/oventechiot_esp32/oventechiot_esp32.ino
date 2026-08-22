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
 */

#include <WiFi.h>
#include <WiFiManager.h>          // tzapu/WiFiManager
#include <HTTPClient.h>
#include <ArduinoJson.h>          // bblanchon/ArduinoJson v6
#include <Preferences.h>
#include <ModbusMaster.h>         // 4-20ma/ModbusMaster

// ================== AYARLANABİLİR SABİTLER ==================
#define PIN_RS485_RX     16   // ESP32 RX2  <- MAX485 RO
#define PIN_RS485_TX     17   // ESP32 TX2  -> MAX485 DI
#define PIN_RS485_DE_RE   4   // MAX485 DE+RE (yön kontrolü)
#define PIN_RESET_BUTON   0   // BOOT butonu — açılışta 5sn basılıysa ayarları sıfırlar

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

void kurulumPortaliBaslat(bool zorlaSifirla) {
  WiFiManager wm;
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
  if (cihazKimlik.length() == 0) return false;

  HTTPClient http;
  String url = sunucuAdresi + "/esp32/" + cihazKimlik + "/tagler";
  http.begin(url);
  int kod = http.GET();
  if (kod != 200) {
    Serial.printf("Tag listesi alinamadi, HTTP %d\n", kod);
    http.end();
    return false;
  }

  String govde = http.getString();
  http.end();

  DynamicJsonDocument doc(8192);
  DeserializationError hata = deserializeJson(doc, govde);
  if (hata) {
    Serial.print("JSON parse hatasi (tagler): "); Serial.println(hata.c_str());
    return false;
  }

  JsonArray dizi = doc["tagler"].as<JsonArray>();
  tagSayisi = 0;
  for (JsonObject t : dizi) {
    if (tagSayisi >= MAKS_TAG_SAYISI) break;
    TagTanimi &hedef = tagListesi[tagSayisi];
    hedef.id = t["id"] | 0;
    strlcpy(hedef.ad, (t["ad"] | ""), sizeof(hedef.ad));
    hedef.modbusAdres = (uint16_t)((t["modbus_adres"] | "0").as<String>().toInt());
    strlcpy(hedef.veriTipi, (t["veri_tipi"] | "bool"), sizeof(hedef.veriTipi));
    strlcpy(hedef.erisim, (t["erisim"] | "read"), sizeof(hedef.erisim));
    hedef.cift_registerli = tipCiftRegisterli(hedef.veriTipi);
    tagSayisi++;
  }
  Serial.printf("Tag listesi alindi: %d tag\n", tagSayisi);
  return true;
}

// ================== MODBUS: TEK BİR TAG'İ PLC'DEN OKU ==================
// Sonucu string olarak döner ("1"/"0" bool için, sayı float/int için).
// Okuma başarısızsa boş string döner (o tag bu turda gönderilmez).
String tagOku(const TagTanimi &tag) {
  String tip = String(tag.veriTipi); tip.toLowerCase();

  if (tip == "bool") {
    uint8_t sonuc = modbus.readCoils(tag.modbusAdres, 1);
    if (sonuc != modbus.ku8MBSuccess) return "";
    return (modbus.getResponseBuffer(0) & 0x01) ? "1" : "0";
  }

  if (tip == "string") {
    return ""; // string tag'ler Modbus register haritasında proje bazlı değişir — burada desteklenmiyor
  }

  if (tag.cift_registerli) {
    uint8_t sonuc = modbus.readHoldingRegisters(tag.modbusAdres, 2);
    if (sonuc != modbus.ku8MBSuccess) return "";
    uint32_t ham = ((uint32_t)modbus.getResponseBuffer(0) << 16) | modbus.getResponseBuffer(1);
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
  if (sonuc != modbus.ku8MBSuccess) return "";
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
    modbus.setTransmitBuffer(0, (uint16_t)(ham >> 16));
    modbus.setTransmitBuffer(1, (uint16_t)(ham & 0xFFFF));
    uint8_t sonuc = modbus.writeMultipleRegisters(tag.modbusAdres, 2);
    return sonuc == modbus.ku8MBSuccess;
  }

  modbus.setTransmitBuffer(0, (uint16_t)degerStr.toInt());
  uint8_t sonuc = modbus.writeMultipleRegisters(tag.modbusAdres, 1);
  return sonuc == modbus.ku8MBSuccess;
}

// ================== SUNUCUYLA SENKRON (xchange) ==================
void sunucuIleSenkronOl() {
  if (cihazKimlik.length() == 0 || tagSayisi == 0) return;

  // 1) PLC'den oku
  DynamicJsonDocument gonderilecek(4096);
  JsonObject degerler = gonderilecek.createNestedObject("degerler");
  int okunanSayisi = 0;
  for (int i = 0; i < tagSayisi; i++) {
    if (String(tagListesi[i].erisim) == "write") continue; // sadece-yazma tag'i okumaya gerek yok
    String v = tagOku(tagListesi[i]);
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
    return;
  }

  String cevapStr = http.getString();
  http.end();

  DynamicJsonDocument cevap(4096);
  if (deserializeJson(cevap, cevapStr)) {
    Serial.println("xchange cevabi parse edilemedi");
    return;
  }

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

// ================== SETUP / LOOP ==================
void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(PIN_RS485_DE_RE, OUTPUT);
  digitalWrite(PIN_RS485_DE_RE, LOW);
  pinMode(PIN_RESET_BUTON, INPUT_PULLUP);

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
  if (WiFi.status() != WL_CONNECTED) {
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
}
