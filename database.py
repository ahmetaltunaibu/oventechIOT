"""OventechIOT — veritabanı katmanı.

Çoklu müşteri (multi-tenant) modeli: her müşteri bir "proje". Bir projenin
birden fazla kullanıcısı (rol: tasarımcı/operatör) ve birden fazla cihazı
(ESP32 köprü) olabilir. Her cihazın tag'leri (Modbus adres tanımları) ve
sayfaları (element düzeni, JSON) var.

İki ayrı giriş var:
  - Platform yöneticisi (sen/Oventech) — proje bağımsız, tüm projeleri
    görür/yönetir, yeni proje açar. Kimlik bilgisi ortam değişkeninde
    (PLATFORM_ADMIN_USER/PASSWORD), veritabanında bir kaydı yok.
  - Proje kullanıcıları (müşteriler) — sadece kullanıcı adı + şifre ile
    giriş yapar (proje kodu YOK, gereksiz). Bu yüzden kullanıcı adları
    TÜM SİSTEMDE (projeler arası) benzersiz olmak zorunda.
"""
import sqlite3
import hashlib
import secrets
import json
from datetime import datetime

DB_NAME = 'oventechiot.db'


def get_db():
    # Kullanıcı raporu (Render canlı loglarında): "sqlite3.OperationalError:
    # database is locked" — arka planda sürekli yazan demo simülatörü (her
    # ~4sn'de tag başına ayrı bağlantı) ile aynı anda gelen normal web
    # istekleri çakışınca SQLite varsayılan ayarlarla ANINDA bu hatayı
    # veriyordu (busy_timeout=0). İki düzeltme:
    #   - busy_timeout: kilit açılana kadar (5sn'e kadar) BEKLE, hemen hata verme.
    #   - WAL modu: okuyucular yazıcıyı, yazıcı okuyucuları neredeyse hiç
    #     bloklamaz — eşzamanlılık dramatik şekilde artar.
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def _sifre_hashle(sifre: str) -> str:
    return hashlib.sha256(sifre.encode('utf-8')).hexdigest()


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projeler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kod TEXT UNIQUE NOT NULL,
            ad TEXT NOT NULL,
            olusturma_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proje_id INTEGER NOT NULL REFERENCES projeler(id) ON DELETE CASCADE,
            kullanici_adi TEXT UNIQUE NOT NULL,   -- TUM SISTEMDE benzersiz (proje kodu olmadan giris icin)
            sifre_hash TEXT NOT NULL,
            ad_soyad TEXT NOT NULL,
            rol TEXT NOT NULL DEFAULT 'operator',  -- 'tasarimci' | 'operator'
            olusturma_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cihazlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proje_id INTEGER NOT NULL REFERENCES projeler(id) ON DELETE CASCADE,
            cihaz_kimlik TEXT UNIQUE NOT NULL,   -- ESP32'nin sunucuya kendini tanıttığı token
            ad TEXT NOT NULL,
            son_gorulme TIMESTAMP,
            nav_stili TEXT NOT NULL DEFAULT 'ust_sekme',  -- 'ust_sekme' | 'alt_navbar' | 'sandvic' | 'yok'
            olusturma_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Migration: eski cihazlar tablosunda nav_stili kolonu yoksa ekle.
    cihaz_kolonlari = {row[1] for row in cursor.execute("PRAGMA table_info(cihazlar)").fetchall()}
    if cihaz_kolonlari and 'nav_stili' not in cihaz_kolonlari:
        cursor.execute("ALTER TABLE cihazlar ADD COLUMN nav_stili TEXT NOT NULL DEFAULT 'ust_sekme'")

    # Migration: BAŞLANGIÇ SAYFASI — kullanıcı isteği: cihaza tıklayınca
    # doğrudan bu sayfa TAM EKRAN (chrome'suz) açılsın, "gerçek bir program"
    # gibi hissettirsin. Boşsa eski davranış (cihaz yönetim sayfası) devam eder.
    cihaz_kolonlari = {row[1] for row in cursor.execute("PRAGMA table_info(cihazlar)").fetchall()}
    if cihaz_kolonlari and 'baslangic_sayfa' not in cihaz_kolonlari:
        cursor.execute("ALTER TABLE cihazlar ADD COLUMN baslangic_sayfa TEXT")

    # Migration: kullanıcı isteği — (1) fiziksel BOOT butonuna basmadan,
    # sunucudan "WiFi ayarlarını unut, kurulum moduna gir" komutu
    # gönderebilmek (cihaz zaten WiFi'ye bağlıyken çalışır — bağlantı
    # koptuysa komut cihaza ulaşamaz, bu normal); (2) ESP32'nin her
    # senkronda bildirdiği Modbus okuma/yazma sağlığını sunucuda görmek
    # (eskiden sadece Seri Monitör'de görünüyordu, kabloya bağlı olman
    # gerekiyordu).
    cihaz_kolonlari = {row[1] for row in cursor.execute("PRAGMA table_info(cihazlar)").fetchall()}
    if cihaz_kolonlari and 'wifi_sifirlama_istendi' not in cihaz_kolonlari:
        cursor.execute("ALTER TABLE cihazlar ADD COLUMN wifi_sifirlama_istendi INTEGER NOT NULL DEFAULT 0")
    if cihaz_kolonlari and 'son_modbus_saglikli' not in cihaz_kolonlari:
        cursor.execute("ALTER TABLE cihazlar ADD COLUMN son_modbus_saglikli INTEGER")
    if cihaz_kolonlari and 'son_modbus_hata_sayisi' not in cihaz_kolonlari:
        cursor.execute("ALTER TABLE cihazlar ADD COLUMN son_modbus_hata_sayisi INTEGER NOT NULL DEFAULT 0")
    if cihaz_kolonlari and 'son_modbus_hata_mesaji' not in cihaz_kolonlari:
        cursor.execute("ALTER TABLE cihazlar ADD COLUMN son_modbus_hata_mesaji TEXT")
    if cihaz_kolonlari and 'son_modbus_rapor_zamani' not in cihaz_kolonlari:
        cursor.execute("ALTER TABLE cihazlar ADD COLUMN son_modbus_rapor_zamani TIMESTAMP")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tagler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cihaz_id INTEGER NOT NULL REFERENCES cihazlar(id) ON DELETE CASCADE,
            ad TEXT NOT NULL,                    -- tag adı (sayfalarda elementler buna bağlanır)
            modbus_adres TEXT NOT NULL,
            veri_tipi TEXT NOT NULL DEFAULT 'bool',  -- 'bool' | 'int' | 'float'
            erisim TEXT NOT NULL DEFAULT 'read',     -- 'read' | 'write' | 'readwrite'
            olcek_min_raw REAL DEFAULT 0,
            olcek_max_raw REAL DEFAULT 0,
            olcek_min_muh REAL DEFAULT 0,
            olcek_max_muh REAL DEFAULT 0,
            deger TEXT,                          -- ESP32'den en son okunan değer
            yazilacak_deger TEXT,                -- kullanıcının yazmak istediği, ESP32'ye henüz iletilmemiş değer
            deger_zamani TIMESTAMP,
            UNIQUE(cihaz_id, ad)
        )
    ''')

    # Migration: eski tagler tablosunda deger/yazilacak_deger/deger_zamani
    # kolonları yoksa ekle (Step 2 - Button elementi için gerekli).
    mevcut_kolonlar = {row[1] for row in cursor.execute("PRAGMA table_info(tagler)").fetchall()}
    if 'deger' not in mevcut_kolonlar:
        cursor.execute('ALTER TABLE tagler ADD COLUMN deger TEXT')
    if 'yazilacak_deger' not in mevcut_kolonlar:
        cursor.execute('ALTER TABLE tagler ADD COLUMN yazilacak_deger TEXT')
    if 'deger_zamani' not in mevcut_kolonlar:
        cursor.execute('ALTER TABLE tagler ADD COLUMN deger_zamani TIMESTAMP')
    # Migration: grafik geçmişi için tag başına özel kayıt aralığı (saniye).
    # NULL ise global varsayılan (GECMIS_MIN_ARALIK_SN) kullanılır.
    if 'gecmis_araligi_sn' not in mevcut_kolonlar:
        cursor.execute('ALTER TABLE tagler ADD COLUMN gecmis_araligi_sn INTEGER')
    # Migration: kullanıcı isteği — TÜM tag'ler otomatik geçmişe kaydedilmesin,
    # sadece kullanıcının açıkça işaretlediği tag'ler kaydedilsin. Yeni
    # tag'lerde varsayılan KAPALI (0); bu sütun eklenirken zaten geçmişi olan
    # (daha önce kaydedilmiş) tag'ler geriye dönük uyumluluk için aşağıda
    # (tag_deger_gecmis tablosu oluştuktan sonra) otomatik AÇIK işaretlenir —
    # yoksa var olan grafikler bir anda veri almayı keser.
    gecmis_kayit_aktif_yeni_eklendi = 'gecmis_kayit_aktif' not in mevcut_kolonlar
    if gecmis_kayit_aktif_yeni_eklendi:
        cursor.execute('ALTER TABLE tagler ADD COLUMN gecmis_kayit_aktif INTEGER NOT NULL DEFAULT 0')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sayfalar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cihaz_id INTEGER NOT NULL REFERENCES cihazlar(id) ON DELETE CASCADE,
            ad TEXT NOT NULL,                       -- kullanıcıya gösterilen mantıksal sayfa adı
            hedef TEXT NOT NULL DEFAULT 'masaustu',  -- 'masaustu' | 'mobil' — aynı ad farklı düzenler tutabilir
            tuval_w INTEGER NOT NULL DEFAULT 1280,
            tuval_h INTEGER NOT NULL DEFAULT 800,
            arkaplan TEXT NOT NULL DEFAULT '#1e2d3d',
            elementler TEXT NOT NULL DEFAULT '[]',  -- JSON liste: [{id,type,props}, ...]
            guncelleme_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(cihaz_id, ad, hedef)
        )
    ''')

    # Migration: eski sayfalar tablosunda hedef kolonu yoksa (mobil/masaüstü
    # ayrımından önceki şema) yeniden oluştur, eski veriyi 'masaustu' olarak taşı.
    eski_sayfa_sql = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sayfalar'"
    ).fetchone()
    if eski_sayfa_sql and 'hedef' not in eski_sayfa_sql[0]:
        cursor.execute('ALTER TABLE sayfalar RENAME TO sayfalar_eski')
        cursor.execute('''
            CREATE TABLE sayfalar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cihaz_id INTEGER NOT NULL REFERENCES cihazlar(id) ON DELETE CASCADE,
                ad TEXT NOT NULL,
                hedef TEXT NOT NULL DEFAULT 'masaustu',
                tuval_w INTEGER NOT NULL DEFAULT 1280,
                tuval_h INTEGER NOT NULL DEFAULT 800,
                arkaplan TEXT NOT NULL DEFAULT '#1e2d3d',
                elementler TEXT NOT NULL DEFAULT '[]',
                guncelleme_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cihaz_id, ad, hedef)
            )
        ''')
        cursor.execute('''
            INSERT INTO sayfalar (id, cihaz_id, ad, hedef, tuval_w, tuval_h, elementler, guncelleme_zamani)
            SELECT id, cihaz_id, ad, 'masaustu', 700, 480, elementler, guncelleme_zamani FROM sayfalar_eski
        ''')
        cursor.execute('DROP TABLE sayfalar_eski')

    # Migration: eski sayfalar tablosunda arkaplan kolonu yoksa ekle (Sayfa
    # Özellikleri paneli için gerekli).
    sayfa_kolonlari = {row[1] for row in cursor.execute("PRAGMA table_info(sayfalar)").fetchall()}
    if sayfa_kolonlari and 'arkaplan' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN arkaplan TEXT NOT NULL DEFAULT '#1e2d3d'")

    # Migration: arka plan RESMİ (kullanıcı isteği — sadece düz renk değil,
    # sayfaya resim de koyabilmeli). URL olarak saklanır (medya tablosundaki
    # bir /medya/<id> yolu ya da dış URL olabilir).
    sayfa_kolonlari = {row[1] for row in cursor.execute("PRAGMA table_info(sayfalar)").fetchall()}
    if sayfa_kolonlari and 'arkaplan_resim' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN arkaplan_resim TEXT")
    if sayfa_kolonlari and 'arkaplan_sigdirma' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN arkaplan_sigdirma TEXT NOT NULL DEFAULT 'cover'")

    # Migration: sayfa arka planı için çift renk gradient seçeneği.
    sayfa_kolonlari = {row[1] for row in cursor.execute("PRAGMA table_info(sayfalar)").fetchall()}
    if sayfa_kolonlari and 'arkaplan_gradient_aktif' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN arkaplan_gradient_aktif INTEGER NOT NULL DEFAULT 0")
    if sayfa_kolonlari and 'arkaplan_gradient_renk1' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN arkaplan_gradient_renk1 TEXT NOT NULL DEFAULT '#1e2d3d'")
    if sayfa_kolonlari and 'arkaplan_gradient_renk2' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN arkaplan_gradient_renk2 TEXT NOT NULL DEFAULT '#0f1720'")
    if sayfa_kolonlari and 'arkaplan_gradient_yon' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN arkaplan_gradient_yon TEXT NOT NULL DEFAULT 'to bottom'")

    # Migration: BAĞLANTILI ŞABLON — kullanıcı isteği: bir sayfa başka bir
    # sayfayı "şablon" olarak seçtiğinde artık bir kerelik KOPYA değil,
    # kalıcı bir BAĞLANTI kurulsun; bağlantılı sayfanın elementleri sadece
    # KAYNAK sayfada düzenlenebilsin, bağlantılı sayfada düzenleme kilitli
    # olsun. sablon_kaynak_* dolu ise bu sayfa "bağlantılı"dır.
    sayfa_kolonlari = {row[1] for row in cursor.execute("PRAGMA table_info(sayfalar)").fetchall()}
    if sayfa_kolonlari and 'sablon_kaynak_cihaz_id' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN sablon_kaynak_cihaz_id INTEGER")
    if sayfa_kolonlari and 'sablon_kaynak_sayfa_ad' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN sablon_kaynak_sayfa_ad TEXT")

    # Migration: sayfa TÜRÜ (normal | popup — popup sayfalar başka bir
    # sayfadan "Sayfaya Git" butonuyla açılınca tam navigasyon yerine
    # üzerinde açılan bir kutu/overlay olarak gösterilir) ve GİRİŞ
    # ANİMASYONU (sayfa açılırken oynatılan kayma/solma efekti).
    sayfa_kolonlari = {row[1] for row in cursor.execute("PRAGMA table_info(sayfalar)").fetchall()}
    if sayfa_kolonlari and 'sayfa_turu' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN sayfa_turu TEXT NOT NULL DEFAULT 'normal'")
    if sayfa_kolonlari and 'giris_animasyonu' not in sayfa_kolonlari:
        cursor.execute("ALTER TABLE sayfalar ADD COLUMN giris_animasyonu TEXT NOT NULL DEFAULT 'none'")

    # Resim elementi icin yuklenen dosyalar — DB icinde BLOB olarak saklanir
    # (Render'in diski deploy'da sifirlaniyor, ama tum .db zaten yedekleniyor
    # — boylece yuklenen resimler de yedekle birlikte tasinir).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS medya (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cihaz_id INTEGER NOT NULL REFERENCES cihazlar(id) ON DELETE CASCADE,
            dosya_adi TEXT NOT NULL,
            mime_tipi TEXT NOT NULL,
            veri BLOB NOT NULL,
            olusturma_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Migration: eski kullanicilar tablosu UNIQUE(proje_id, kullanici_adi)
    # ile olusmus olabilir (kod hala proje_kodu istiyordu) - artik kullanici
    # adi TUM SISTEMDE benzersiz olmali (proje kodu olmadan giris icin).
    eski_sql = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='kullanicilar'"
    ).fetchone()
    if eski_sql and 'UNIQUE(proje_id, kullanici_adi)' in eski_sql[0]:
        cursor.execute('ALTER TABLE kullanicilar RENAME TO kullanicilar_eski')
        cursor.execute('''
            CREATE TABLE kullanicilar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proje_id INTEGER NOT NULL REFERENCES projeler(id) ON DELETE CASCADE,
                kullanici_adi TEXT UNIQUE NOT NULL,
                sifre_hash TEXT NOT NULL,
                ad_soyad TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'operator',
                olusturma_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            INSERT OR IGNORE INTO kullanicilar
            SELECT id, proje_id, kullanici_adi, sifre_hash, ad_soyad, rol, olusturma_zamani
            FROM kullanicilar_eski
        ''')
        cursor.execute('DROP TABLE kullanicilar_eski')

    # Grafik elementi için TAG DEĞER GEÇMİŞİ — kullanıcı isteği: grafik artık
    # sadece o an açık kalan sekmede biriken anlık değerleri değil, gerçek
    # geçmiş veriyi göstermeli. Her tag_deger_guncelle() çağrısında (ESP32'den
    # gelen okuma ya da demo simülatörü) buraya bir satır düşer; eski satırlar
    # tag başına belli bir sayıda tutulup budanır (sınırsız büyümesin diye).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tag_deger_gecmis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id INTEGER NOT NULL REFERENCES tagler(id) ON DELETE CASCADE,
            deger TEXT,
            zaman TIMESTAMP DEFAULT (datetime('now', '+3 hours'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gecmis_tag_zaman ON tag_deger_gecmis(tag_id, zaman)')

    # Kullanıcı isteği: grafikte 1 hafta/1 ay/3 ay gibi uzun aralıklar da
    # gösterilebilsin — ama ham (saniyelik) veriyi aylarca tutmak veritabanını
    # şişirir. Bu yüzden ham veri sadece son ~30 saat tutulur (bkz.
    # GECMIS_HAM_SAKLAMA_SAAT), her saat kapandığında o saatin özeti
    # (ortalama/min/maks) burada kalıcı olarak saklanır — 1 hafta = 168 satır,
    # 3 ay = ~2160 satır/tag gibi makul boyutlarda kalır.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tag_deger_gecmis_saatlik (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id INTEGER NOT NULL REFERENCES tagler(id) ON DELETE CASCADE,
            saat TIMESTAMP NOT NULL,
            ort_deger REAL,
            min_deger REAL,
            maks_deger REAL,
            adet INTEGER,
            UNIQUE(tag_id, saat)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_gecmis_saatlik_tag_saat ON tag_deger_gecmis_saatlik(tag_id, saat)')

    # gecmis_kayit_aktif sütunu YENİ eklendiyse (yukarıda) — tag_deger_gecmis
    # artık kesin var olduğuna göre, halihazırda geçmişi olan tag'leri
    # "kayıt aktif" say (geriye dönük uyumluluk, bkz. yukarıdaki açıklama).
    if gecmis_kayit_aktif_yeni_eklendi:
        cursor.execute('''
            UPDATE tagler SET gecmis_kayit_aktif = 1
            WHERE id IN (SELECT DISTINCT tag_id FROM tag_deger_gecmis)
        ''')

    # ALARM — "Alarm Oluşturma" elementiyle tanımlanan kurallar (bir tag'in
    # hangi değerde/eşikte alarm sayılacağı) ve gerçekleşen alarm kayıtları.
    # Değerlendirme SUNUCU tarafında yapılır (ESP32'den her xchange'de) —
    # böylece tarayıcı hiç açık olmasa bile alarm sunucuda kayıt altına alınır.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alarm_kurallari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eleman_id TEXT UNIQUE NOT NULL,   -- sayfa üzerindeki "alarm" elementinin id'si
            cihaz_id INTEGER NOT NULL REFERENCES cihazlar(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tagler(id) ON DELETE CASCADE,
            tip TEXT NOT NULL DEFAULT 'bool',     -- 'bool' | 'esik'
            bool_tetik_deger TEXT DEFAULT '1',    -- bool tip: bu değere eşitse alarm
            karsilastirma TEXT DEFAULT '>',       -- esik tip: > < >= <= ==
            esik_deger REAL DEFAULT 0,
            mesaj TEXT DEFAULT '',
            guncelleme_zamani TIMESTAMP DEFAULT (datetime('now', '+3 hours'))
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alarm_kayitlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cihaz_id INTEGER NOT NULL REFERENCES cihazlar(id) ON DELETE CASCADE,
            kural_id INTEGER REFERENCES alarm_kurallari(id) ON DELETE SET NULL,
            tag_id INTEGER REFERENCES tagler(id) ON DELETE SET NULL,
            mesaj TEXT,
            deger TEXT,
            olusma_zamani TIMESTAMP DEFAULT (datetime('now', '+3 hours')),
            giderilme_zamani TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alarm_kayit_cihaz ON alarm_kayitlari(cihaz_id, olusma_zamani)')

    # ESP32 UZAKTAN GÜNCELLEME (OTA) — gemba/gemba-iot-gateway'deki çalışan
    # sistemden esinlenildi. Dosyalar (medya tablosundaki gibi) BLOB olarak
    # saklanıyor — Render'ın diski her deploy'da sıfırlanıyor, .db her zaman
    # yedekleniyor. cihaz_id NULL ise bu firmware o PROJENİN tüm cihazlarını
    # hedefler ("ALL"); dolu ise sadece o cihazı.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS firmware_dosyalari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proje_id INTEGER NOT NULL REFERENCES projeler(id) ON DELETE CASCADE,
            cihaz_id INTEGER REFERENCES cihazlar(id) ON DELETE CASCADE,
            dosya_adi TEXT NOT NULL,
            versiyon TEXT NOT NULL DEFAULT '1.0.0',
            aciklama TEXT,
            boyut INTEGER,
            md5_hash TEXT,
            veri BLOB NOT NULL,
            aktif INTEGER NOT NULL DEFAULT 1,
            yukleme_zamani TIMESTAMP DEFAULT (datetime('now', '+3 hours'))
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS firmware_gecmisi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firmware_id INTEGER REFERENCES firmware_dosyalari(id) ON DELETE CASCADE,
            cihaz_id INTEGER NOT NULL REFERENCES cihazlar(id) ON DELETE CASCADE,
            durum TEXT NOT NULL,   -- 'indiriliyor' | 'basarili' | 'hata'
            hata_mesaji TEXT,
            zaman TIMESTAMP DEFAULT (datetime('now', '+3 hours'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fw_gecmis_cihaz ON firmware_gecmisi(cihaz_id, zaman)')

    conn.commit()
    conn.close()


# ============================================================
# PROJE
# ============================================================

def proje_listesi():
    """Platform yöneticisi için — tüm projeler + cihaz/kullanıcı sayıları."""
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT p.*,
                   (SELECT COUNT(*) FROM cihazlar c WHERE c.proje_id = p.id) AS cihaz_sayisi,
                   (SELECT COUNT(*) FROM kullanicilar k WHERE k.proje_id = p.id) AS kullanici_sayisi
            FROM projeler p ORDER BY p.ad
        ''').fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def proje_getir(proje_id: int):
    conn = get_db()
    try:
        row = conn.execute('SELECT * FROM projeler WHERE id = ?', (proje_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def proje_kullanicilari(proje_id: int):
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT id, kullanici_adi, ad_soyad, rol FROM kullanicilar WHERE proje_id = ? ORDER BY kullanici_adi',
            (proje_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def proje_ekle(kod: str, ad: str):
    conn = get_db()
    try:
        cur = conn.execute('INSERT INTO projeler (kod, ad) VALUES (?, ?)', (kod.strip(), ad.strip()))
        conn.commit()
        return True, cur.lastrowid
    except sqlite3.IntegrityError:
        return False, 'Bu proje kodu zaten kullanılıyor'
    finally:
        conn.close()


def proje_yeniden_adlandir(proje_id: int, yeni_ad: str):
    conn = get_db()
    try:
        conn.execute('UPDATE projeler SET ad = ? WHERE id = ?', (yeni_ad.strip(), proje_id))
        conn.commit()
        return True
    finally:
        conn.close()


def proje_sil(proje_id: int):
    """Projeyi ve ona bağlı tüm kullanıcı/cihaz/tag/sayfaları siler (ON DELETE CASCADE)."""
    conn = get_db()
    try:
        conn.execute('DELETE FROM projeler WHERE id = ?', (proje_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def _medya_url_yenile(url, medya_id_eslesme):
    """"/medya/<eski_id>" biçimindeki URL'leri yeni medya id'sine çevirir —
    proje_kopyala() kopyalanan sayfalardaki resim referanslarının kırık
    kalmaması için kullanır. Dış URL'lere ya da None'a dokunmaz."""
    if not url or not isinstance(url, str) or not url.startswith('/medya/'):
        return url
    id_kismi = url[len('/medya/'):]
    if id_kismi.isdigit() and int(id_kismi) in medya_id_eslesme:
        return '/medya/' + str(medya_id_eslesme[int(id_kismi)])
    return url


def _element_referanslarini_yenile(el, tag_id_eslesme, medya_id_eslesme):
    """proje_kopyala() için — bir elementin (ve grafik seriler / durum
    listesi gibi iç içe yapıların) tag_id ve resim_url referanslarını
    YENİ (kopyalanan) tag/medya id'lerine göre yeniden yazar. Eşlemede
    olmayan (bilinmeyen) bir referansa dokunmaz."""
    el = dict(el)
    if el.get('tag_id') in tag_id_eslesme:
        el['tag_id'] = tag_id_eslesme[el['tag_id']]
    if el.get('gizle_tag_id') in tag_id_eslesme:
        el['gizle_tag_id'] = tag_id_eslesme[el['gizle_tag_id']]
    for alan in ('resim_url', 'resim_url_acik', 'resim_url_kapali'):
        if el.get(alan):
            el[alan] = _medya_url_yenile(el[alan], medya_id_eslesme)
    if el.get('seriler'):  # grafik elementi — her serinin kendi tag_id'si
        yeni_seriler = []
        for s in el['seriler']:
            s2 = dict(s)
            if s2.get('tag_id') in tag_id_eslesme:
                s2['tag_id'] = tag_id_eslesme[s2['tag_id']]
            yeni_seriler.append(s2)
        el['seriler'] = yeni_seriler
    if el.get('durumlar'):  # durum göstergesi — her durumun kendi tag_id/resim_url'i
        yeni_durumlar = []
        for d in el['durumlar']:
            d2 = dict(d)
            if d2.get('tag_id') in tag_id_eslesme:
                d2['tag_id'] = tag_id_eslesme[d2['tag_id']]
            if d2.get('resim_url'):
                d2['resim_url'] = _medya_url_yenile(d2['resim_url'], medya_id_eslesme)
            yeni_durumlar.append(d2)
        el['durumlar'] = yeni_durumlar
    return el


def proje_kopyala(kaynak_proje_id: int, yeni_kod: str, yeni_ad: str):
    """Bir projenin TÜM cihazlarını, tag'lerini, yüklü medyasını (resimler)
    ve sayfalarını (elementler JSON'undaki tag_id/resim referansları YENİ
    id'lere göre yeniden yazılarak) YENİ bir projeye kopyalar — yeni bir
    müşteri/kurulum için şablon olarak kullanmak amacıyla.

    Kasıtlı olarak KOPYALANMAYANLAR:
      - Kullanıcılar (giriş hesapları) — kullanıcı adı tüm sistemde
        benzersiz olmak zorunda, yeni projede sıfırdan açılır.
      - Canlı/geçmiş veri (tag değerleri, tag_deger_gecmis, alarm_kayitlari,
        son_gorulme, Modbus durumu) — yeni cihazlar henüz hiçbir fiziksel
        donanıma bağlı değil, bunlar anlamsız olurdu.
      - Cihaz kimlikleri YENİDEN üretilir (secrets.token_hex) — kopyalanan
        her cihaz kaydı başlangıçta HİÇBİR ESP32'ye bağlı değildir, yeni
        fiziksel cihaz kurulurken bu kimlik firmware'e girilmelidir.

    Alarm kuralları (alarm_kurallari) ayrıca kopyalanmaz — sayfalar
    kopyalanırken üzerlerindeki 'alarm' elementleri için otomatik yeniden
    oluşturulur (bkz. pages/sayfa.py'deki aynı mantık, burada tekrarlanıyor)."""
    conn = get_db()
    try:
        cur = conn.execute('INSERT INTO projeler (kod, ad) VALUES (?, ?)', (yeni_kod.strip(), yeni_ad.strip()))
        yeni_proje_id = cur.lastrowid

        kaynak_cihazlar = conn.execute('SELECT * FROM cihazlar WHERE proje_id = ?', (kaynak_proje_id,)).fetchall()
        cihaz_id_eslesme = {}   # eski cihaz_id -> yeni cihaz_id
        tag_id_eslesme = {}     # eski tag_id -> yeni tag_id
        medya_id_eslesme = {}   # eski medya_id -> yeni medya_id

        for c in kaynak_cihazlar:
            yeni_kimlik = secrets.token_hex(12)
            cur = conn.execute('''
                INSERT INTO cihazlar (proje_id, cihaz_kimlik, ad, nav_stili, baslangic_sayfa)
                VALUES (?, ?, ?, ?, ?)
            ''', (yeni_proje_id, yeni_kimlik, c['ad'], c['nav_stili'], c['baslangic_sayfa']))
            cihaz_id_eslesme[c['id']] = cur.lastrowid

        for eski_cihaz_id, yeni_cihaz_id in cihaz_id_eslesme.items():
            for t in conn.execute('SELECT * FROM tagler WHERE cihaz_id = ?', (eski_cihaz_id,)).fetchall():
                cur = conn.execute('''
                    INSERT INTO tagler (cihaz_id, ad, modbus_adres, veri_tipi, erisim,
                                         olcek_min_raw, olcek_max_raw, olcek_min_muh, olcek_max_muh,
                                         gecmis_araligi_sn, gecmis_kayit_aktif)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (yeni_cihaz_id, t['ad'], t['modbus_adres'], t['veri_tipi'], t['erisim'],
                      t['olcek_min_raw'], t['olcek_max_raw'], t['olcek_min_muh'], t['olcek_max_muh'],
                      t['gecmis_araligi_sn'], t['gecmis_kayit_aktif']))
                tag_id_eslesme[t['id']] = cur.lastrowid

        for eski_cihaz_id, yeni_cihaz_id in cihaz_id_eslesme.items():
            for m in conn.execute('SELECT * FROM medya WHERE cihaz_id = ?', (eski_cihaz_id,)).fetchall():
                cur = conn.execute(
                    'INSERT INTO medya (cihaz_id, dosya_adi, mime_tipi, veri) VALUES (?, ?, ?, ?)',
                    (yeni_cihaz_id, m['dosya_adi'], m['mime_tipi'], m['veri'])
                )
                medya_id_eslesme[m['id']] = cur.lastrowid

        conn.commit()
    finally:
        conn.close()

    # Sayfalar — ayrı bir bağlantıyla, zaten var olan sayfa_kaydet() /
    # alarm_kural_kaydet() / sayfa_baglantili_olustur() fonksiyonları
    # üzerinden (aynı doğrulanmış mantığı tekrar icat etmemek için).
    for eski_cihaz_id, yeni_cihaz_id in cihaz_id_eslesme.items():
        conn2 = get_db()
        try:
            sayfalar = conn2.execute('SELECT * FROM sayfalar WHERE cihaz_id = ?', (eski_cihaz_id,)).fetchall()
        finally:
            conn2.close()
        for s in sayfalar:
            if s['sablon_kaynak_cihaz_id'] and s['sablon_kaynak_sayfa_ad']:
                # Bağlantılı (şablon) sayfa — içeriği yok, sadece bağlantıyı
                # kopyala. Kaynak cihaz da bu kopyalamanın içindeyse YENİ
                # kopyasına bağla, değilse (proje dışı bir kaynak — normalde
                # olmaz ama güvenlik payı) eski kaynağa bağlı kalır.
                kaynak_cihaz = cihaz_id_eslesme.get(s['sablon_kaynak_cihaz_id'], s['sablon_kaynak_cihaz_id'])
                sayfa_baglantili_olustur(yeni_cihaz_id, s['ad'], kaynak_cihaz, s['sablon_kaynak_sayfa_ad'])
                continue
            elementler = json.loads(s['elementler'])
            elementler = [_element_referanslarini_yenile(el, tag_id_eslesme, medya_id_eslesme) for el in elementler]
            sayfa_kaydet(
                yeni_cihaz_id, s['ad'], elementler, hedef=s['hedef'],
                tuval_w=s['tuval_w'], tuval_h=s['tuval_h'], arkaplan=s['arkaplan'],
                arkaplan_resim=_medya_url_yenile(s['arkaplan_resim'], medya_id_eslesme),
                arkaplan_sigdirma=s['arkaplan_sigdirma'],
                arkaplan_gradient_aktif=bool(s['arkaplan_gradient_aktif']),
                arkaplan_gradient_renk1=s['arkaplan_gradient_renk1'],
                arkaplan_gradient_renk2=s['arkaplan_gradient_renk2'],
                arkaplan_gradient_yon=s['arkaplan_gradient_yon'],
                sayfa_turu=s['sayfa_turu'], giris_animasyonu=s['giris_animasyonu'],
            )
            for el in elementler:
                if el.get('type') == 'alarm' and el.get('tag_id'):
                    alarm_kural_kaydet(
                        el['id'], yeni_cihaz_id, el['tag_id'], el.get('tip') or 'bool',
                        bool_tetik_deger=el.get('bool_tetik_deger') or '1',
                        karsilastirma=el.get('karsilastirma') or '>',
                        esik_deger=el.get('esik_deger') or 0,
                        mesaj=el.get('mesaj') or '',
                    )

    return True, yeni_proje_id


def cihaz_kopyala(kaynak_cihaz_id: int, yeni_ad: str = None):
    """Kullanıcı isteği: proje_kopyala() ile AYNI mantık ama TEK bir cihaz
    için — kaynak cihazla AYNI projede yeni bir cihaz oluşturur: aynı
    tag'ler, aynı yüklü medya (resimler), aynı sayfalar (tag_id/resim_url
    referansları yeni id'lere göre yeniden yazılarak). Yeni cihaza YENİ/BOŞ
    bir cihaz_kimlik verilir (henüz hiçbir ESP32'ye bağlı değil). Canlı/
    geçmiş veri (tag değerleri, alarm kayıtları, son_gorulme, Modbus
    durumu) kopyalanmaz — bkz. proje_kopyala docstring, aynı gerekçe."""
    conn = get_db()
    try:
        kaynak = conn.execute('SELECT * FROM cihazlar WHERE id = ?', (kaynak_cihaz_id,)).fetchone()
        if not kaynak:
            return False, 'Cihaz bulunamadı'
        yeni_kimlik = secrets.token_hex(12)
        yeni_ad_deger = (yeni_ad or '').strip() or f"{kaynak['ad']} (kopya)"
        cur = conn.execute('''
            INSERT INTO cihazlar (proje_id, cihaz_kimlik, ad, nav_stili, baslangic_sayfa)
            VALUES (?, ?, ?, ?, ?)
        ''', (kaynak['proje_id'], yeni_kimlik, yeni_ad_deger, kaynak['nav_stili'], kaynak['baslangic_sayfa']))
        yeni_cihaz_id = cur.lastrowid

        tag_id_eslesme = {}
        for t in conn.execute('SELECT * FROM tagler WHERE cihaz_id = ?', (kaynak_cihaz_id,)).fetchall():
            cur = conn.execute('''
                INSERT INTO tagler (cihaz_id, ad, modbus_adres, veri_tipi, erisim,
                                     olcek_min_raw, olcek_max_raw, olcek_min_muh, olcek_max_muh,
                                     gecmis_araligi_sn, gecmis_kayit_aktif)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (yeni_cihaz_id, t['ad'], t['modbus_adres'], t['veri_tipi'], t['erisim'],
                  t['olcek_min_raw'], t['olcek_max_raw'], t['olcek_min_muh'], t['olcek_max_muh'],
                  t['gecmis_araligi_sn'], t['gecmis_kayit_aktif']))
            tag_id_eslesme[t['id']] = cur.lastrowid

        medya_id_eslesme = {}
        for m in conn.execute('SELECT * FROM medya WHERE cihaz_id = ?', (kaynak_cihaz_id,)).fetchall():
            cur = conn.execute(
                'INSERT INTO medya (cihaz_id, dosya_adi, mime_tipi, veri) VALUES (?, ?, ?, ?)',
                (yeni_cihaz_id, m['dosya_adi'], m['mime_tipi'], m['veri'])
            )
            medya_id_eslesme[m['id']] = cur.lastrowid

        sayfalar = conn.execute('SELECT * FROM sayfalar WHERE cihaz_id = ?', (kaynak_cihaz_id,)).fetchall()
        conn.commit()
    finally:
        conn.close()

    # Sayfalar — bağlantılı (şablon) sayfalarda kaynak cihaz kopyalanan
    # cihazın KENDİSİ olmadığı sürece (normal durum — bir cihaz kendi
    # kendine şablon olmaz) referans AYNEN korunur, yeniden eşleme gerekmez.
    for s in sayfalar:
        if s['sablon_kaynak_cihaz_id'] and s['sablon_kaynak_sayfa_ad']:
            sayfa_baglantili_olustur(yeni_cihaz_id, s['ad'], s['sablon_kaynak_cihaz_id'], s['sablon_kaynak_sayfa_ad'])
            continue
        elementler = json.loads(s['elementler'])
        elementler = [_element_referanslarini_yenile(el, tag_id_eslesme, medya_id_eslesme) for el in elementler]
        sayfa_kaydet(
            yeni_cihaz_id, s['ad'], elementler, hedef=s['hedef'],
            tuval_w=s['tuval_w'], tuval_h=s['tuval_h'], arkaplan=s['arkaplan'],
            arkaplan_resim=_medya_url_yenile(s['arkaplan_resim'], medya_id_eslesme),
            arkaplan_sigdirma=s['arkaplan_sigdirma'],
            arkaplan_gradient_aktif=bool(s['arkaplan_gradient_aktif']),
            arkaplan_gradient_renk1=s['arkaplan_gradient_renk1'],
            arkaplan_gradient_renk2=s['arkaplan_gradient_renk2'],
            arkaplan_gradient_yon=s['arkaplan_gradient_yon'],
            sayfa_turu=s['sayfa_turu'], giris_animasyonu=s['giris_animasyonu'],
        )
        for el in elementler:
            if el.get('type') == 'alarm' and el.get('tag_id'):
                alarm_kural_kaydet(
                    el['id'], yeni_cihaz_id, el['tag_id'], el.get('tip') or 'bool',
                    bool_tetik_deger=el.get('bool_tetik_deger') or '1',
                    karsilastirma=el.get('karsilastirma') or '>',
                    esik_deger=el.get('esik_deger') or 0,
                    mesaj=el.get('mesaj') or '',
                )

    return True, yeni_cihaz_id


def proje_getir_kod(kod: str):
    conn = get_db()
    try:
        row = conn.execute('SELECT * FROM projeler WHERE kod = ?', (kod.strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ============================================================
# KULLANICI
# ============================================================

def kullanici_ekle(proje_id: int, kullanici_adi: str, sifre: str, ad_soyad: str, rol: str = 'operator'):
    conn = get_db()
    try:
        cur = conn.execute('''
            INSERT INTO kullanicilar (proje_id, kullanici_adi, sifre_hash, ad_soyad, rol)
            VALUES (?, ?, ?, ?, ?)
        ''', (proje_id, kullanici_adi.strip(), _sifre_hashle(sifre), ad_soyad.strip(), rol))
        conn.commit()
        return True, cur.lastrowid
    except sqlite3.IntegrityError:
        return False, 'Bu kullanıcı adı zaten kullanılıyor (tüm sistemde benzersiz olmalı)'
    finally:
        conn.close()


def kullanici_guncelle(kullanici_id: int, ad_soyad: str, rol: str, yeni_sifre: str = None):
    conn = get_db()
    try:
        if yeni_sifre:
            conn.execute(
                'UPDATE kullanicilar SET ad_soyad = ?, rol = ?, sifre_hash = ? WHERE id = ?',
                (ad_soyad.strip(), rol, _sifre_hashle(yeni_sifre), kullanici_id)
            )
        else:
            conn.execute(
                'UPDATE kullanicilar SET ad_soyad = ?, rol = ? WHERE id = ?',
                (ad_soyad.strip(), rol, kullanici_id)
            )
        conn.commit()
        return True
    finally:
        conn.close()


def kullanici_sil(kullanici_id: int):
    conn = get_db()
    try:
        conn.execute('DELETE FROM kullanicilar WHERE id = ?', (kullanici_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def giris_dogrula(kullanici_adi: str, sifre: str):
    """Proje kullanıcısı girişi — sadece kullanıcı adı + şifre. Başarılıysa
    (proje_dict, kullanici_dict) döner, değilse (None, None)."""
    conn = get_db()
    try:
        kullanici = conn.execute('''
            SELECT * FROM kullanicilar WHERE kullanici_adi = ? AND sifre_hash = ?
        ''', (kullanici_adi.strip(), _sifre_hashle(sifre))).fetchone()
        if not kullanici:
            return None, None
        proje = conn.execute('SELECT * FROM projeler WHERE id = ?', (kullanici['proje_id'],)).fetchone()
        return (dict(proje) if proje else None), dict(kullanici)
    finally:
        conn.close()


# ============================================================
# CIHAZ
# ============================================================

def cihaz_ekle(proje_id: int, ad: str, cihaz_kimlik: str = None):
    """cihaz_kimlik verilmezse otomatik üretilir (ESP32 firmware'ine bu değer
    girilecek). Verilirse (örn. daha önce kurulmuş, flash'ında hâlâ eski
    kimliği taşıyan bir ESP32'yi veri kaybı sonrası yeniden eşlemek için)
    AYNEN o değer kullanılır — cihazın fiziksel olarak tekrar kurulum
    portalından geçirilmesine gerek kalmaz."""
    cihaz_kimlik = (cihaz_kimlik or '').strip() or secrets.token_hex(12)
    conn = get_db()
    try:
        cur = conn.execute('''
            INSERT INTO cihazlar (proje_id, cihaz_kimlik, ad) VALUES (?, ?, ?)
        ''', (proje_id, cihaz_kimlik, ad.strip()))
        conn.commit()
        return True, {'id': cur.lastrowid, 'cihaz_kimlik': cihaz_kimlik}
    except sqlite3.IntegrityError:
        return False, ('Bu cihaz kimliği zaten kullanılıyor (başka bir cihazda kayıtlı) '
                        'ya da proje artık bulunamıyor. Kimliği kontrol edip tekrar deneyin.')
    finally:
        conn.close()


def proje_cihazlari(proje_id: int):
    conn = get_db()
    try:
        rows = conn.execute('SELECT * FROM cihazlar WHERE proje_id = ? ORDER BY ad', (proje_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def cihaz_getir_kimlik(cihaz_kimlik: str):
    conn = get_db()
    try:
        row = conn.execute('SELECT * FROM cihazlar WHERE cihaz_kimlik = ?', (cihaz_kimlik,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def cihaz_yeniden_adlandir(cihaz_id: int, yeni_ad: str):
    conn = get_db()
    try:
        conn.execute('UPDATE cihazlar SET ad = ? WHERE id = ?', (yeni_ad.strip(), cihaz_id))
        conn.commit()
        return True
    finally:
        conn.close()


def cihaz_baslangic_sayfa_guncelle(cihaz_id: int, sayfa_ad: str):
    """Boş string/None verilirse başlangıç sayfası kaldırılır (eski davranışa
    — cihaz yönetim sayfasına dönülür)."""
    conn = get_db()
    try:
        conn.execute('UPDATE cihazlar SET baslangic_sayfa = ? WHERE id = ?',
                     (sayfa_ad.strip() if sayfa_ad and sayfa_ad.strip() else None, cihaz_id))
        conn.commit()
        return True
    finally:
        conn.close()




def cihaz_sil(cihaz_id: int):
    """Cihazı ve ona bağlı tag/sayfaları siler (ON DELETE CASCADE)."""
    conn = get_db()
    try:
        conn.execute('DELETE FROM cihazlar WHERE id = ?', (cihaz_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def cihaz_son_gorulme_guncelle(cihaz_kimlik: str):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE cihazlar SET son_gorulme = datetime('now', '+3 hours') WHERE cihaz_kimlik = ?",
            (cihaz_kimlik,)
        )
        conn.commit()
    finally:
        conn.close()


def cihaz_wifi_sifirlama_iste(cihaz_id: int):
    """Cihaz yönetim sayfasındaki "WiFi Ayarlarını Uzaktan Sıfırla" butonu
    çağırır — bir bayrak işaretler, ESP32 bir sonraki senkronunda (WiFi
    hâlâ açıksa) bunu görüp kendi WiFi ayarlarını unutup kurulum moduna
    girer. Cihaz zaten offline'sa bu komut kendisine hiç ulaşmaz."""
    conn = get_db()
    try:
        conn.execute('UPDATE cihazlar SET wifi_sifirlama_istendi = 1 WHERE id = ?', (cihaz_id,))
        conn.commit()
    finally:
        conn.close()


def cihaz_wifi_sifirlama_durumu_al_ve_temizle(cihaz_kimlik: str) -> bool:
    """ESP32'nin xchange isteğinde çağrılır — bayrak açıksa True döner VE
    hemen sıfırlar (tek seferlik, tekrar tekrar yeniden başlatmasın diye)."""
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT id, wifi_sifirlama_istendi FROM cihazlar WHERE cihaz_kimlik = ?', (cihaz_kimlik,)
        ).fetchone()
        if not row or not row['wifi_sifirlama_istendi']:
            return False
        conn.execute('UPDATE cihazlar SET wifi_sifirlama_istendi = 0 WHERE id = ?', (row['id'],))
        conn.commit()
        return True
    finally:
        conn.close()


def cihaz_modbus_durumu_guncelle(cihaz_kimlik: str, saglikli: bool, hata_sayisi: int, hata_mesaji: str):
    """ESP32 her senkronda bu turun Modbus okuma/yazma sonucunu bildirir —
    eskiden sadece Seri Monitör'de görünüyordu, artık sunucuda (cihaz
    yönetim sayfasında) da görülebiliyor."""
    conn = get_db()
    try:
        conn.execute('''
            UPDATE cihazlar
            SET son_modbus_saglikli = ?, son_modbus_hata_sayisi = ?, son_modbus_hata_mesaji = ?,
                son_modbus_rapor_zamani = datetime('now', '+3 hours')
            WHERE cihaz_kimlik = ?
        ''', (1 if saglikli else 0, hata_sayisi, hata_mesaji or None, cihaz_kimlik))
        conn.commit()
    finally:
        conn.close()


def cihaz_son_gorulme_saniye_once(cihaz_id: int):
    """Kullanıcı isteği: ekranda 'cihazdan en son ne zaman veri alındı'
    gösterilebilsin — bu veri zaten var (ESP32 her istekte son_gorulme'yi
    günceller), yeni bir tag/Modbus adresi gerekmiyor. 'now' ile aynı
    +3 saat kaydırması uygulanıyor ki fark doğru çıksın (ikisi de aynı
    ofsetle hesaplanınca ofset iptal oluyor)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT CAST((strftime('%s', 'now', '+3 hours') - strftime('%s', son_gorulme)) AS INTEGER) AS saniye_once "
            "FROM cihazlar WHERE id = ? AND son_gorulme IS NOT NULL",
            (cihaz_id,)
        ).fetchone()
        return row['saniye_once'] if row else None
    finally:
        conn.close()


# ============================================================
# TAG
# ============================================================

def tag_ekle(cihaz_id: int, ad: str, modbus_adres: str, veri_tipi: str = 'bool', erisim: str = 'read', gecmis_araligi_sn=None, gecmis_kayit_aktif=False):
    conn = get_db()
    try:
        cur = conn.execute('''
            INSERT INTO tagler (cihaz_id, ad, modbus_adres, veri_tipi, erisim, gecmis_araligi_sn, gecmis_kayit_aktif)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (cihaz_id, ad.strip(), modbus_adres.strip(), veri_tipi, erisim, gecmis_araligi_sn, 1 if gecmis_kayit_aktif else 0))
        conn.commit()
        return True, cur.lastrowid
    except sqlite3.IntegrityError:
        return False, 'Bu isimde bir tag bu cihazda zaten var'
    finally:
        conn.close()


def tag_guncelle(tag_id: int, ad: str, modbus_adres: str, veri_tipi: str, erisim: str, gecmis_araligi_sn=None, gecmis_kayit_aktif=False):
    conn = get_db()
    try:
        conn.execute('''
            UPDATE tagler SET ad = ?, modbus_adres = ?, veri_tipi = ?, erisim = ?, gecmis_araligi_sn = ?, gecmis_kayit_aktif = ?
            WHERE id = ?
        ''', (ad.strip(), modbus_adres.strip(), veri_tipi, erisim, gecmis_araligi_sn, 1 if gecmis_kayit_aktif else 0, tag_id))
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, 'Bu isimde bir tag bu cihazda zaten var'
    finally:
        conn.close()


def cihaz_tagleri(cihaz_id: int):
    conn = get_db()
    try:
        rows = conn.execute('SELECT * FROM tagler WHERE cihaz_id = ? ORDER BY ad', (cihaz_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================
# MEDYA (Resim elementi icin yuklenen dosyalar)
# ============================================================

def medya_yukle(cihaz_id: int, dosya_adi: str, mime_tipi: str, veri: bytes):
    conn = get_db()
    try:
        cur = conn.execute(
            'INSERT INTO medya (cihaz_id, dosya_adi, mime_tipi, veri) VALUES (?, ?, ?, ?)',
            (cihaz_id, dosya_adi, mime_tipi, veri)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def medya_getir(medya_id: int):
    conn = get_db()
    try:
        row = conn.execute('SELECT * FROM medya WHERE id = ?', (medya_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ============================================================
# FIRMWARE (ESP32 uzaktan güncelleme / OTA)
# ============================================================

def firmware_yukle(proje_id: int, cihaz_id, dosya_adi: str, veri: bytes,
                    versiyon: str = '1.0.0', aciklama: str = ''):
    """Yeni firmware yükler. cihaz_id None ise projedeki TÜM cihazları
    hedefler. Aynı hedefe (proje+cihaz) ait önceki aktif firmware'ler
    otomatik pasife çekilir — bir hedefte aynı anda tek aktif sürüm olur."""
    conn = get_db()
    try:
        md5_hash = hashlib.md5(veri).hexdigest()
        if cihaz_id:
            conn.execute(
                'UPDATE firmware_dosyalari SET aktif = 0 WHERE proje_id = ? AND cihaz_id = ? AND aktif = 1',
                (proje_id, cihaz_id)
            )
        else:
            conn.execute(
                'UPDATE firmware_dosyalari SET aktif = 0 WHERE proje_id = ? AND cihaz_id IS NULL AND aktif = 1',
                (proje_id,)
            )
        cur = conn.execute('''
            INSERT INTO firmware_dosyalari (proje_id, cihaz_id, dosya_adi, versiyon, aciklama, boyut, md5_hash, veri, aktif)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        ''', (proje_id, cihaz_id, dosya_adi, versiyon.strip() or '1.0.0', aciklama, len(veri), md5_hash, veri))
        conn.commit()
        return True, cur.lastrowid
    finally:
        conn.close()


def firmware_listesi(proje_id: int):
    """Yönetim ekranı için — dosya BLOB'u hariç metadata listesi."""
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT f.id, f.proje_id, f.cihaz_id, f.dosya_adi, f.versiyon, f.aciklama,
                   f.boyut, f.md5_hash, f.aktif, f.yukleme_zamani, c.ad AS cihaz_ad
            FROM firmware_dosyalari f
            LEFT JOIN cihazlar c ON c.id = f.cihaz_id
            WHERE f.proje_id = ?
            ORDER BY f.yukleme_zamani DESC
        ''', (proje_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def firmware_getir(firmware_id: int):
    """BLOB dahil tam kayıt — indirme (ESP32'ye gönderme) için."""
    conn = get_db()
    try:
        row = conn.execute('SELECT * FROM firmware_dosyalari WHERE id = ?', (firmware_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def firmware_aktiflik_ayarla(firmware_id: int, aktif: bool):
    conn = get_db()
    try:
        conn.execute('UPDATE firmware_dosyalari SET aktif = ? WHERE id = ?', (1 if aktif else 0, firmware_id))
        conn.commit()
        return True
    finally:
        conn.close()


def firmware_sil(firmware_id: int):
    conn = get_db()
    try:
        conn.execute('DELETE FROM firmware_dosyalari WHERE id = ?', (firmware_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def firmware_kontrol(cihaz_id: int, mevcut_versiyon: str = ''):
    """Bir cihaz için uygulanacak aktif firmware var mı? Önce CİHAZA ÖZEL
    aktif firmware'e bakar, yoksa PROJENİN TÜMÜNE (cihaz_id NULL) aktif
    firmware'ine bakar — ama 'ALL' hedefliyse bu cihaz onu daha önce
    başarıyla almışsa BİR DAHA teklif etmez (gemba'daki 'ALL' mantığıyla
    aynı). Cihazın gönderdiği versiyon sunucudakinden büyük/eşitse None döner."""
    conn = get_db()
    try:
        cihaz = conn.execute('SELECT proje_id FROM cihazlar WHERE id = ?', (cihaz_id,)).fetchone()
        if not cihaz:
            return None
        fw = conn.execute(
            'SELECT * FROM firmware_dosyalari WHERE cihaz_id = ? AND aktif = 1 ORDER BY yukleme_zamani DESC LIMIT 1',
            (cihaz_id,)
        ).fetchone()
        if not fw:
            fw = conn.execute(
                'SELECT * FROM firmware_dosyalari WHERE proje_id = ? AND cihaz_id IS NULL AND aktif = 1 ORDER BY yukleme_zamani DESC LIMIT 1',
                (cihaz['proje_id'],)
            ).fetchone()
            if fw:
                zaten_aldi = conn.execute(
                    "SELECT 1 FROM firmware_gecmisi WHERE cihaz_id = ? AND firmware_id = ? AND durum = 'basarili' LIMIT 1",
                    (cihaz_id, fw['id'])
                ).fetchone()
                if zaten_aldi:
                    return None
        if not fw:
            return None

        if mevcut_versiyon:
            def _ver_tuple(v):
                try:
                    return tuple(int(x) for x in v.strip().lstrip('v').split('.'))
                except (ValueError, AttributeError):
                    return (0,)
            if _ver_tuple(fw['versiyon']) <= _ver_tuple(mevcut_versiyon):
                return None
        return dict(fw)
    finally:
        conn.close()


def firmware_gecmis_kaydet(cihaz_id: int, firmware_id: int, durum: str, hata_mesaji: str = None):
    conn = get_db()
    try:
        conn.execute(
            'INSERT INTO firmware_gecmisi (cihaz_id, firmware_id, durum, hata_mesaji) VALUES (?, ?, ?, ?)',
            (cihaz_id, firmware_id, durum, hata_mesaji)
        )
        conn.commit()
    finally:
        conn.close()


def firmware_gecmisi_listele(cihaz_id: int, limit: int = 30):
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT g.*, f.dosya_adi, f.versiyon
            FROM firmware_gecmisi g
            LEFT JOIN firmware_dosyalari f ON f.id = g.firmware_id
            WHERE g.cihaz_id = ?
            ORDER BY g.id DESC LIMIT ?
        ''', (cihaz_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def tag_sil(tag_id: int):
    conn = get_db()
    try:
        conn.execute('DELETE FROM tagler WHERE id = ?', (tag_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def tag_getir(tag_id: int):
    conn = get_db()
    try:
        row = conn.execute('SELECT * FROM tagler WHERE id = ?', (tag_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def tag_yaz_iste(tag_id: int, deger):
    """Kullanıcı bir butona bastı — ESP32 bir sonraki xchange'de bunu okuyup
    PLC'ye yazacak (ESP32 tarafı ayrı bir görev). Değer string olarak saklanır."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE tagler SET yazilacak_deger = ? WHERE id = ?",
            (str(deger), tag_id)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def tagler_yazilacak_temizle(tag_idleri):
    """Kullanıcı raporu: PLC'yi SCADA'nın yanı sıra bir de kendi fiziksel
    HMI paneli kontrol ediyor — SCADA'nın yazdığı bir değer temizlenmediği
    için ESP32'ye HER xchange döngüsünde (5sn'de bir) tekrar tekrar
    gönderiliyordu, bu da PLC'ye sürekli zorla yazılıp HMI'den yapılan
    değişiklikleri eziyordu. Doğru davranış: SCADA bir tag'i SADECE BİR KEZ
    yazsın, sonraki bir kullanıcı işlemine kadar sadece okusun. Bu yüzden
    bir tag'in yazilacak_deger'i ESP32'ye 'yazilacaklar' olarak gönderilir
    gönderilmez (bkz. pages/esp32.py: esp32_xchange) burada temizlenir."""
    if not tag_idleri:
        return
    conn = get_db()
    try:
        conn.executemany(
            "UPDATE tagler SET yazilacak_deger = NULL WHERE id = ?",
            [(tid,) for tid in tag_idleri]
        )
        conn.commit()
    finally:
        conn.close()


# Kullanıcı isteği: grafik geçmişine her canlı güncellemede değil, en fazla
# şu aralıkla bir satır düşsün (10-30sn aralığı istendi, ortası seçildi) —
# ESP32/simülatör çok daha sık senkron olsa bile veritabanı şişmesin. Bu
# GLOBAL varsayılan; tag'in kendi gecmis_araligi_sn'i doluysa o kullanılır.
GECMIS_MIN_ARALIK_SN = 20

# Ham (saniyelik/dakikalık) veri en az bu kadar saat tutulur — "24 saat"
# butonuna biraz tampon payı bırakmak için 24'ten biraz fazla.
GECMIS_HAM_SAKLAMA_SAAT = 30
# Saatlik özet veri en az bu kadar gün tutulur — "3 ay" butonu 90 gün ister,
# tampon payı bırakıldı.
GECMIS_SAATLIK_SAKLAMA_GUN = 100
# Sorgu sonucu tek seferde en fazla bu kadar satır dönsün (uç durumda çok
# sık örnekleme + uzun aralık seçilse bile tarayıcı/DB aşırı yüklenmesin).
GECMIS_SORGU_MAKS_SATIR = 20000

# Grafik elementindeki hazır zaman aralığı butonları: ad -> (saniye, kaynak).
# 'ham' -> tag_deger_gecmis (ince taneli), 'saatlik' -> tag_deger_gecmis_saatlik
# (saatlik ortalama) tablosundan okunur.
ARALIK_TANIMLARI = {
    '15dk': (900, 'ham'),
    '30dk': (1800, 'ham'),
    '1sa': (3600, 'ham'),
    '3sa': (10800, 'ham'),
    '6sa': (21600, 'ham'),
    '12sa': (43200, 'ham'),
    '24sa': (86400, 'ham'),
    '1hf': (604800, 'saatlik'),
    '1ay': (2592000, 'saatlik'),
    '3ay': (7776000, 'saatlik'),
}


def tag_deger_guncelle(tag_id: int, deger):
    """ESP32'den gelen okunan değeri kaydeder (xchange sırasında kullanılacak).
    Bu HER ZAMAN tagler.deger'i (anlık, ekranda gösterilen değer) günceller.
    Geçmişe (tag_deger_gecmis) satır düşmesi ise AYRI bir açık tercih —
    kullanıcı isteği: tüm tag'ler otomatik kaydedilmesin, sadece tag'in
    gecmis_kayit_aktif'i açıksa (tag tablosundaki onay kutusu) kaydedilir;
    o zaman da en fazla tag'in kendi gecmis_araligi_sn'i (yoksa
    GECMIS_MIN_ARALIK_SN) kadar sıklıkla. Bir saat kapandığında o saatin
    özeti tag_deger_gecmis_saatlik'e düşer (uzun vadeli, kompakt saklama —
    bkz. 1hf/1ay/3ay butonları) ve eski satırlar budanır."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE tagler SET deger = ?, deger_zamani = datetime('now', '+3 hours') WHERE id = ?",
            (str(deger), tag_id)
        )
        tag_row = conn.execute(
            "SELECT gecmis_araligi_sn, gecmis_kayit_aktif FROM tagler WHERE id = ?", (tag_id,)
        ).fetchone()
        if tag_row and tag_row['gecmis_kayit_aktif']:
            aralik_sn = (tag_row['gecmis_araligi_sn'] if tag_row['gecmis_araligi_sn'] else GECMIS_MIN_ARALIK_SN)
            son = conn.execute(
                "SELECT zaman FROM tag_deger_gecmis WHERE tag_id = ? ORDER BY id DESC LIMIT 1",
                (tag_id,)
            ).fetchone()
            yeterince_eski = (son is None) or conn.execute(
                "SELECT (julianday(datetime('now', '+3 hours')) - julianday(?)) * 86400 >= ?",
                (son['zaman'], aralik_sn)
            ).fetchone()[0]
            if yeterince_eski:
                conn.execute(
                    "INSERT INTO tag_deger_gecmis (tag_id, deger) VALUES (?, ?)",
                    (tag_id, str(deger))
                )

            # Tamamlanmış son saatin özetini çıkar (varsa ve daha önce
            # yapılmadıysa — UNIQUE(tag_id, saat) + INSERT OR IGNORE sayesinde
            # aynı saat için tekrar tekrar hesaplanmaz). Sadece son ~2 saatlik
            # pencereyi tarıyor (idx_gecmis_tag_zaman ile ucuz).
            once = conn.total_changes
            conn.execute('''
                INSERT OR IGNORE INTO tag_deger_gecmis_saatlik (tag_id, saat, ort_deger, min_deger, maks_deger, adet)
                SELECT tag_id,
                       strftime('%Y-%m-%d %H:00:00', zaman) AS saat_grubu,
                       AVG(CAST(deger AS REAL)), MIN(CAST(deger AS REAL)), MAX(CAST(deger AS REAL)), COUNT(*)
                FROM tag_deger_gecmis
                WHERE tag_id = ?
                  AND zaman >= datetime('now', '+3 hours', '-2 hours')
                  AND zaman <  strftime('%Y-%m-%d %H:00:00', datetime('now', '+3 hours'))
                GROUP BY saat_grubu
            ''', (tag_id,))
            saat_kapandi = conn.total_changes > once

            if saat_kapandi:
                # Saatte bir kez tetiklenen ucuz bir bakım penceresi — ham
                # veriyi ve çok eski saatlik özetleri buda.
                conn.execute(
                    "DELETE FROM tag_deger_gecmis WHERE tag_id = ? AND zaman < datetime('now', '+3 hours', ?)",
                    (tag_id, f'-{GECMIS_HAM_SAKLAMA_SAAT} hours')
                )
                conn.execute(
                    "DELETE FROM tag_deger_gecmis_saatlik WHERE tag_id = ? AND saat < datetime('now', '+3 hours', ?)",
                    (tag_id, f'-{GECMIS_SAATLIK_SAKLAMA_GUN} days')
                )
        conn.commit()
    finally:
        conn.close()
    # Alarm değerlendirmesi ayrı bir bağlantıyla (yukarıdaki commit'ten
    # SONRA) yapılıyor — tarayıcı hiç açık olmasa bile burada tetiklenir.
    alarm_degerlendir(tag_id, deger)
    return True


def tag_deger_gecmisi(tag_id: int, limit: int = 100):
    """Bir tag'in en son `limit` geçmiş değerini ESKİDEN YENİYE sıralı döner:
    [{deger, zaman}, ...] — grafik elementinin "canlı" (varsayılan) modu
    bunu kullanır."""
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT deger, zaman FROM tag_deger_gecmis WHERE tag_id = ? ORDER BY id DESC LIMIT ?',
            (tag_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def tagler_deger_gecmisi(tag_idler: list, limit: int = 100):
    """Birden fazla tag için geçmiş değerleri tek seferde döner:
    {tag_id: [{deger, zaman}, ...]} — grafik elementinde çoklu seri desteği için."""
    return {tid: tag_deger_gecmisi(tid, limit) for tid in tag_idler}


def tag_deger_gecmisi_araliktan(tag_id: int, aralik: str):
    """Hazır zaman aralığı adına göre (bkz. ARALIK_TANIMLARI — 15dk..3ay)
    ham ya da saatlik özet tablodan geçmiş döner: [{deger, zaman}, ...]
    eskiden yeniye. Bilinmeyen aralık adı gelirse boş liste döner."""
    tanim = ARALIK_TANIMLARI.get(aralik)
    if not tanim:
        return []
    saniye, kaynak = tanim
    conn = get_db()
    try:
        if kaynak == 'ham':
            rows = conn.execute('''
                SELECT deger, zaman FROM (
                    SELECT id, deger, zaman FROM tag_deger_gecmis
                    WHERE tag_id = ? AND zaman >= datetime('now', '+3 hours', ?)
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
            ''', (tag_id, f'-{saniye} seconds', GECMIS_SORGU_MAKS_SATIR)).fetchall()
        else:
            rows = conn.execute('''
                SELECT ort_deger AS deger, saat AS zaman FROM (
                    SELECT id, ort_deger, saat FROM tag_deger_gecmis_saatlik
                    WHERE tag_id = ? AND saat >= datetime('now', '+3 hours', ?)
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
            ''', (tag_id, f'-{saniye} seconds', GECMIS_SORGU_MAKS_SATIR)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def tagler_deger_gecmisi_araliktan(tag_idler: list, aralik: str):
    """tag_deger_gecmisi_araliktan'ın çoklu-tag hali (grafik elementi çoklu
    seri için): {tag_id: [{deger, zaman}, ...]}."""
    return {tid: tag_deger_gecmisi_araliktan(tid, aralik) for tid in tag_idler}


# ============================================================
# ALARM — "Alarm Oluşturma" elementiyle tanımlanan kurallar, sunucu
# tarafında değerlendirilir (tarayıcı açık olmasa bile çalışır).
# ============================================================

def alarm_kural_kaydet(eleman_id: str, cihaz_id: int, tag_id: int, tip: str,
                        bool_tetik_deger: str = '1', karsilastirma: str = '>',
                        esik_deger: float = 0, mesaj: str = ''):
    """Sayfa kaydedilirken üzerindeki her 'alarm' elementi için çağrılır —
    eleman_id UNIQUE olduğu için var olan kural güncellenir, yoksa eklenir."""
    conn = get_db()
    try:
        conn.execute('''
            INSERT INTO alarm_kurallari (eleman_id, cihaz_id, tag_id, tip, bool_tetik_deger, karsilastirma, esik_deger, mesaj, guncelleme_zamani)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+3 hours'))
            ON CONFLICT(eleman_id) DO UPDATE SET
                cihaz_id = excluded.cihaz_id, tag_id = excluded.tag_id, tip = excluded.tip,
                bool_tetik_deger = excluded.bool_tetik_deger, karsilastirma = excluded.karsilastirma,
                esik_deger = excluded.esik_deger, mesaj = excluded.mesaj,
                guncelleme_zamani = datetime('now', '+3 hours')
        ''', (eleman_id, cihaz_id, tag_id, tip, str(bool_tetik_deger), karsilastirma, esik_deger, mesaj))
        conn.commit()
    finally:
        conn.close()


def alarm_kurallari_temizle_disinda(cihaz_id: int, gecerli_eleman_idler: list):
    """Sayfa kaydedilirken artık tuvalde olmayan (silinmiş) alarm elementlerinin
    kurallarını temizler — cihaz bazlı, diğer sayfaların kurallarına dokunmaz
    olsun diye çağıran taraf o SAYFANIN eleman id'lerini + diğer sayfalardaki
    kuralları birleştirip göndermeli. Basitlik için: sadece bu fonksiyona
    verilmeyen VE cihaza ait olan kurallar silinir."""
    conn = get_db()
    try:
        if gecerli_eleman_idler:
            soru = ','.join('?' * len(gecerli_eleman_idler))
            conn.execute(
                f'DELETE FROM alarm_kurallari WHERE cihaz_id = ? AND eleman_id NOT IN ({soru})',
                [cihaz_id] + gecerli_eleman_idler
            )
        else:
            conn.execute('DELETE FROM alarm_kurallari WHERE cihaz_id = ?', (cihaz_id,))
        conn.commit()
    finally:
        conn.close()


def alarm_kurallari_tag_icin(tag_id: int):
    conn = get_db()
    try:
        rows = conn.execute('SELECT * FROM alarm_kurallari WHERE tag_id = ?', (tag_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _alarm_tetiklendi_mi(kural: dict, deger) -> bool:
    try:
        if kural['tip'] == 'bool':
            return str(deger) == str(kural['bool_tetik_deger'])
        sayi = float(deger)
        esik = float(kural['esik_deger'])
        k = kural['karsilastirma']
        if k == '>': return sayi > esik
        if k == '<': return sayi < esik
        if k == '>=': return sayi >= esik
        if k == '<=': return sayi <= esik
        if k == '==': return sayi == esik
    except (TypeError, ValueError):
        return False
    return False


def alarm_degerlendir(tag_id: int, deger):
    """Bir tag'in değeri güncellenince (ESP32 xchange ya da simülatör) bu tag'e
    bağlı tüm alarm kurallarını değerlendirir. Alarm YENİ tetiklendiyse
    (önceden açık bir kaydı yoksa) alarm_kayitlari'na satır ekler; alarm
    koşulu artık sağlanmıyorsa açık kaydı 'giderildi' olarak kapatır."""
    kurallar = alarm_kurallari_tag_icin(tag_id)
    if not kurallar:
        return
    conn = get_db()
    try:
        for kural in kurallar:
            tetik = _alarm_tetiklendi_mi(kural, deger)
            acik = conn.execute(
                'SELECT id FROM alarm_kayitlari WHERE kural_id = ? AND giderilme_zamani IS NULL ORDER BY id DESC LIMIT 1',
                (kural['id'],)
            ).fetchone()
            if tetik and not acik:
                conn.execute(
                    'INSERT INTO alarm_kayitlari (cihaz_id, kural_id, tag_id, mesaj, deger) VALUES (?, ?, ?, ?, ?)',
                    (kural['cihaz_id'], kural['id'], tag_id, kural['mesaj'] or '', str(deger))
                )
            elif not tetik and acik:
                conn.execute(
                    "UPDATE alarm_kayitlari SET giderilme_zamani = datetime('now', '+3 hours') WHERE id = ?",
                    (acik['id'],)
                )
        conn.commit()
    finally:
        conn.close()


def alarm_kayitlari_listele(cihaz_id: int, limit: int = 50):
    """En yeni alarm kayıtlarını (aktif + geçmiş) tag adıyla birlikte döner."""
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT ak.*, t.ad AS tag_ad
            FROM alarm_kayitlari ak
            LEFT JOIN tagler t ON t.id = ak.tag_id
            WHERE ak.cihaz_id = ?
            ORDER BY ak.id DESC LIMIT ?
        ''', (cihaz_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def alarm_aktif_sayisi(cihaz_id: int) -> int:
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT COUNT(*) AS n FROM alarm_kayitlari WHERE cihaz_id = ? AND giderilme_zamani IS NULL',
            (cihaz_id,)
        ).fetchone()
        return row['n'] if row else 0
    finally:
        conn.close()


def cihaz_tag_degerleri(cihaz_id: int):
    """Runtime sayfaların değer okuması için: {tag_id: {ad, deger, yazilacak_deger, deger_zamani}}"""
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT id, ad, deger, yazilacak_deger, deger_zamani FROM tagler WHERE cihaz_id = ?',
            (cihaz_id,)
        ).fetchall()
        return {r['id']: dict(r) for r in rows}
    finally:
        conn.close()


# ============================================================
# SAYFA (element düzeni — JSON)
# ============================================================

def sayfa_kaydet(cihaz_id: int, ad: str, elementler: list, hedef: str = 'masaustu',
                  tuval_w: int = None, tuval_h: int = None, arkaplan: str = None,
                  arkaplan_resim: str = None, arkaplan_sigdirma: str = None,
                  arkaplan_gradient_aktif: bool = None, arkaplan_gradient_renk1: str = None,
                  arkaplan_gradient_renk2: str = None, arkaplan_gradient_yon: str = None,
                  sayfa_turu: str = None, giris_animasyonu: str = None):
    varsayilan_w = 1280 if hedef == 'masaustu' else 420
    varsayilan_h = 800 if hedef == 'masaustu' else 860
    conn = get_db()
    try:
        conn.execute('''
            INSERT INTO sayfalar (cihaz_id, ad, hedef, tuval_w, tuval_h, arkaplan, arkaplan_resim, arkaplan_sigdirma,
                                   arkaplan_gradient_aktif, arkaplan_gradient_renk1, arkaplan_gradient_renk2, arkaplan_gradient_yon,
                                   sayfa_turu, giris_animasyonu, elementler, guncelleme_zamani)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+3 hours'))
            ON CONFLICT(cihaz_id, ad, hedef) DO UPDATE SET
                elementler = excluded.elementler,
                tuval_w = excluded.tuval_w,
                tuval_h = excluded.tuval_h,
                arkaplan = excluded.arkaplan,
                arkaplan_resim = excluded.arkaplan_resim,
                arkaplan_sigdirma = excluded.arkaplan_sigdirma,
                arkaplan_gradient_aktif = excluded.arkaplan_gradient_aktif,
                arkaplan_gradient_renk1 = excluded.arkaplan_gradient_renk1,
                arkaplan_gradient_renk2 = excluded.arkaplan_gradient_renk2,
                arkaplan_gradient_yon = excluded.arkaplan_gradient_yon,
                sayfa_turu = excluded.sayfa_turu,
                giris_animasyonu = excluded.giris_animasyonu,
                guncelleme_zamani = datetime('now', '+3 hours')
        ''', (cihaz_id, ad.strip(), hedef, tuval_w or varsayilan_w, tuval_h or varsayilan_h,
              arkaplan or '#1e2d3d', (arkaplan_resim or None), arkaplan_sigdirma or 'cover',
              1 if arkaplan_gradient_aktif else 0, arkaplan_gradient_renk1 or '#1e2d3d',
              arkaplan_gradient_renk2 or '#0f1720', arkaplan_gradient_yon or 'to bottom',
              sayfa_turu or 'normal', giris_animasyonu or 'none',
              json.dumps(elementler, ensure_ascii=False)))
        conn.commit()
        return True
    finally:
        conn.close()


def sayfa_getir(cihaz_id: int, ad: str, hedef: str = 'masaustu'):
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT * FROM sayfalar WHERE cihaz_id = ? AND ad = ? AND hedef = ?',
            (cihaz_id, ad.strip(), hedef)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d['elementler'] = json.loads(d['elementler'])
        return d
    finally:
        conn.close()


def sayfa_sil(cihaz_id: int, ad: str):
    """Sayfanın TÜM düzenlerini (masaüstü + mobil) siler."""
    conn = get_db()
    try:
        conn.execute('DELETE FROM sayfalar WHERE cihaz_id = ? AND ad = ?', (cihaz_id, ad.strip()))
        conn.commit()
        return True
    finally:
        conn.close()


def sayfa_varyant_sil(cihaz_id: int, ad: str, hedef: str):
    """Sayfanın sadece tek bir düzenini (örn. sadece mobil) siler."""
    conn = get_db()
    try:
        conn.execute('DELETE FROM sayfalar WHERE cihaz_id = ? AND ad = ? AND hedef = ?', (cihaz_id, ad.strip(), hedef))
        conn.commit()
        return True
    finally:
        conn.close()


def sayfa_adini_degistir(cihaz_id: int, eski_ad: str, yeni_ad: str):
    """Sayfanın adını değiştirir — HEM masaüstü HEM mobil düzenini birlikte
    (ikisi aynı mantıksal sayfayı temsil ediyor, bkz. sayfa_sil). Ayrıca
    aynı cihazın DİĞER sayfalarındaki 'Sayfaya Git' butonlarının
    hedef_sayfa referanslarını da günceller — yoksa isim değişince o
    butonlar kırık/hedefsiz kalırdı."""
    eski_ad = eski_ad.strip()
    yeni_ad = yeni_ad.strip()
    if not yeni_ad:
        return False, 'Yeni sayfa adı boş olamaz.'
    if yeni_ad == eski_ad:
        return False, 'Yeni ad, mevcut adla aynı.'
    conn = get_db()
    try:
        var_mi = conn.execute(
            'SELECT 1 FROM sayfalar WHERE cihaz_id = ? AND ad = ?', (cihaz_id, yeni_ad)
        ).fetchone()
        if var_mi:
            return False, f'"{yeni_ad}" adında bir sayfa zaten var.'
        etkilenen = conn.execute(
            'UPDATE sayfalar SET ad = ? WHERE cihaz_id = ? AND ad = ?', (yeni_ad, cihaz_id, eski_ad)
        ).rowcount
        if not etkilenen:
            conn.rollback()
            return False, 'Sayfa bulunamadı.'
        # Diğer sayfalardaki "Sayfaya Git" butonlarının hedefini güncelle.
        satirlar = conn.execute(
            'SELECT id, elementler FROM sayfalar WHERE cihaz_id = ?', (cihaz_id,)
        ).fetchall()
        for satir in satirlar:
            try:
                elementler = json.loads(satir['elementler'] or '[]')
            except (TypeError, ValueError):
                continue
            degisti = False
            for el in elementler:
                if el.get('type') == 'button' and el.get('hedef_sayfa') == eski_ad:
                    el['hedef_sayfa'] = yeni_ad
                    degisti = True
            if degisti:
                conn.execute(
                    'UPDATE sayfalar SET elementler = ? WHERE id = ?',
                    (json.dumps(elementler, ensure_ascii=False), satir['id'])
                )
        conn.commit()
        return True, None
    finally:
        conn.close()


def cihaz_sayfalari(cihaz_id: int):
    """Sayfa adlarını, hangi düzenlerin (masaustu/mobil) mevcut olduğunu ve
    en son güncelleme zamanını gruplanmış şekilde döner."""
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT ad, hedef, guncelleme_zamani FROM sayfalar WHERE cihaz_id = ? ORDER BY ad',
            (cihaz_id,)
        ).fetchall()
        gruplar = {}
        for r in rows:
            g = gruplar.setdefault(r['ad'], {'ad': r['ad'], 'hedefler': [], 'guncelleme_zamani': r['guncelleme_zamani']})
            g['hedefler'].append(r['hedef'])
            if r['guncelleme_zamani'] and r['guncelleme_zamani'] > (g['guncelleme_zamani'] or ''):
                g['guncelleme_zamani'] = r['guncelleme_zamani']
        return sorted(gruplar.values(), key=lambda g: g['ad'])
    finally:
        conn.close()


def cihaz_sayfa_bilgileri(cihaz_id: int):
    """Bir cihazın TÜM sayfaları için {ad: {tur, animasyon}} döner —
    runtime'da 'Sayfaya Git' butonu tıklanınca hedef sayfa 'popup' türündeyse
    tam navigasyon yerine üstte açılan bir kutu (overlay) olarak gösterilir;
    ekstra bir istek atmadan bunu bilebilmek için tüm sayfa tipleri tek
    seferde bu şekilde runtime'a gömülür."""
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT ad, sayfa_turu, giris_animasyonu FROM sayfalar WHERE cihaz_id = ? GROUP BY ad',
            (cihaz_id,)
        ).fetchall()
        return {r['ad']: {'tur': r['sayfa_turu'] or 'normal', 'animasyon': r['giris_animasyonu'] or 'none'} for r in rows}
    finally:
        conn.close()


def demo_projesini_sil():
    """Daha önce demo amaçlı otomatik oluşturulan 'demo' projesini (varsa)
    kalıcı olarak siler — kullanıcı isteği: "demoyu sil tamamen". Cascade
    silme (kullanicilar/cihazlar/tagler/sayfalar/alarm_*/tag_deger_gecmis)
    zaten FOREIGN KEY ... ON DELETE CASCADE ile tanımlı, tek satır yeterli.
    Ayrıca bu, demo simülatörünün production'da sürekli DB'ye yazıp
    "database is locked" hatalarına yol açan arka plan thread'ini de
    kalıcı olarak devre dışı bırakır (artık hiç başlatılmıyor)."""
    proje = proje_getir_kod('demo')
    if not proje:
        return
    conn = get_db()
    try:
        conn.execute('DELETE FROM projeler WHERE id = ?', (proje['id'],))
        conn.commit()
    finally:
        conn.close()


def proje_tum_sayfalari(proje_id: int):
    """Projedeki TÜM cihazların TÜM sayfalarını (şablon seçimi için) döner:
    [{cihaz_id, cihaz_ad, sayfa_ad}, ...] — her mantıksal sayfa adı bir kez
    (hangi düzenleri olduğuna bakılmaksızın). Kendisi zaten başka bir
    sayfaya BAĞLI (linked) sayfalar listelenmez — zincirleme bağlantı
    (A→B→C) oluşmasın diye sadece "kök" sayfalar şablon kaynağı olabilir."""
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT DISTINCT s.cihaz_id, c.ad AS cihaz_ad, s.ad AS sayfa_ad
            FROM sayfalar s
            JOIN cihazlar c ON c.id = s.cihaz_id
            WHERE c.proje_id = ? AND s.sablon_kaynak_cihaz_id IS NULL
            ORDER BY c.ad, s.ad
        ''', (proje_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def sayfa_getir_efektif(cihaz_id: int, ad: str, hedef: str = 'masaustu'):
    """sayfa_getir gibi ama sayfa bir ŞABLONA BAĞLIYSA (sablon_kaynak_*
    doluysa) elementler/tuval/arkaplan bilgisini KAYNAK sayfadan çözerek
    döner — kullanıcı isteği: bağlantılı sayfa artık kendi elementlerini
    tutmuyor, kaynaktaki neyse onu gösteriyor (tasarımda salt okunur,
    çalışırken de her zaman kaynaktaki güncel hali)."""
    sayfa = sayfa_getir(cihaz_id, ad, hedef)
    if not sayfa:
        return None
    if sayfa.get('sablon_kaynak_cihaz_id') and sayfa.get('sablon_kaynak_sayfa_ad'):
        kaynak = sayfa_getir(sayfa['sablon_kaynak_cihaz_id'], sayfa['sablon_kaynak_sayfa_ad'], hedef)
        if kaynak:
            sonuc = dict(kaynak)
            sonuc['id'] = sayfa['id']
            sonuc['ad'] = sayfa['ad']
            sonuc['hedef'] = sayfa['hedef']
            sonuc['sablon_baglantili'] = True
            sonuc['sablon_kaynak_cihaz_id'] = sayfa['sablon_kaynak_cihaz_id']
            sonuc['sablon_kaynak_sayfa_ad'] = sayfa['sablon_kaynak_sayfa_ad']
            return sonuc
        # Kaynak silinmiş olabilir — bağlantı koptu say, kendi (boş) verisiyle devam et.
    sayfa['sablon_baglantili'] = False
    return sayfa


def sayfa_baglantili_olustur(cihaz_id: int, ad: str, kaynak_cihaz_id: int, kaynak_sayfa_ad: str):
    """Yeni sayfa oluştururken 'şablon' seçilmişse artık bir kerelik KOPYA
    değil, KALICI BAĞLANTI kurulur — kaynağın hangi düzenleri (masaustu/
    mobil) varsa o düzenler için birer satır açılır, kendi elementler'i
    boş kalır (efektif veri her zaman sayfa_getir_efektif ile kaynaktan
    okunur)."""
    conn = get_db()
    try:
        olusturuldu = False
        for hedef in ('masaustu', 'mobil'):
            kaynak = conn.execute(
                'SELECT * FROM sayfalar WHERE cihaz_id = ? AND ad = ? AND hedef = ?',
                (kaynak_cihaz_id, kaynak_sayfa_ad, hedef)
            ).fetchone()
            if not kaynak:
                continue
            conn.execute('''
                INSERT INTO sayfalar (cihaz_id, ad, hedef, tuval_w, tuval_h, arkaplan, elementler,
                                       sablon_kaynak_cihaz_id, sablon_kaynak_sayfa_ad, guncelleme_zamani)
                VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, datetime('now', '+3 hours'))
                ON CONFLICT(cihaz_id, ad, hedef) DO UPDATE SET
                    sablon_kaynak_cihaz_id = excluded.sablon_kaynak_cihaz_id,
                    sablon_kaynak_sayfa_ad = excluded.sablon_kaynak_sayfa_ad,
                    guncelleme_zamani = datetime('now', '+3 hours')
            ''', (cihaz_id, ad.strip(), hedef, kaynak['tuval_w'], kaynak['tuval_h'], kaynak['arkaplan'],
                  kaynak_cihaz_id, kaynak_sayfa_ad.strip()))
            olusturuldu = True
        conn.commit()
        return olusturuldu
    finally:
        conn.close()


def sayfa_sablonlu_mu(cihaz_id: int, ad: str, hedef: str = 'masaustu'):
    """Bu (cihaz_id, ad, hedef) sayfası başka bir sayfaya BAĞLI mı? Kaynak
    (cihaz_id, sayfa_ad) çiftini döner, bağlı değilse None."""
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT sablon_kaynak_cihaz_id, sablon_kaynak_sayfa_ad FROM sayfalar WHERE cihaz_id = ? AND ad = ? AND hedef = ?',
            (cihaz_id, ad.strip(), hedef)
        ).fetchone()
        if row and row['sablon_kaynak_cihaz_id'] and row['sablon_kaynak_sayfa_ad']:
            return {'cihaz_id': row['sablon_kaynak_cihaz_id'], 'sayfa_ad': row['sablon_kaynak_sayfa_ad']}
        return None
    finally:
        conn.close()


def sayfa_sablon_baglantisi_kaldir(cihaz_id: int, ad: str):
    """Bağlantıyı KOPARIR — kullanıcı isteği: "seçimi de iptal edebilir
    olmamız gerekiyor". Kopma anındaki (kaynaktan gelen) içerik BU sayfanın
    kendi satırına KOPYALANIR ki bağlantı kesilince hiçbir şey kaybolmasın;
    sayfa o andan itibaren bağımsız ve serbestçe düzenlenebilir olur."""
    conn = get_db()
    try:
        for hedef in ('masaustu', 'mobil'):
            row = conn.execute(
                'SELECT * FROM sayfalar WHERE cihaz_id = ? AND ad = ? AND hedef = ?',
                (cihaz_id, ad.strip(), hedef)
            ).fetchone()
            if not row:
                continue
            d = dict(row)
            if not (d.get('sablon_kaynak_cihaz_id') and d.get('sablon_kaynak_sayfa_ad')):
                continue
            kaynak = conn.execute(
                'SELECT * FROM sayfalar WHERE cihaz_id = ? AND ad = ? AND hedef = ?',
                (d['sablon_kaynak_cihaz_id'], d['sablon_kaynak_sayfa_ad'], hedef)
            ).fetchone()
            if kaynak:
                kd = dict(kaynak)
                conn.execute('''
                    UPDATE sayfalar SET
                        elementler = :elementler, tuval_w = :tuval_w, tuval_h = :tuval_h,
                        arkaplan = :arkaplan, arkaplan_resim = :arkaplan_resim, arkaplan_sigdirma = :arkaplan_sigdirma,
                        arkaplan_gradient_aktif = :arkaplan_gradient_aktif, arkaplan_gradient_renk1 = :arkaplan_gradient_renk1,
                        arkaplan_gradient_renk2 = :arkaplan_gradient_renk2, arkaplan_gradient_yon = :arkaplan_gradient_yon,
                        sablon_kaynak_cihaz_id = NULL, sablon_kaynak_sayfa_ad = NULL
                    WHERE id = :id
                ''', {
                    'elementler': kd['elementler'], 'tuval_w': kd['tuval_w'], 'tuval_h': kd['tuval_h'],
                    'arkaplan': kd['arkaplan'], 'arkaplan_resim': kd.get('arkaplan_resim'),
                    'arkaplan_sigdirma': kd.get('arkaplan_sigdirma'),
                    'arkaplan_gradient_aktif': kd.get('arkaplan_gradient_aktif'),
                    'arkaplan_gradient_renk1': kd.get('arkaplan_gradient_renk1'),
                    'arkaplan_gradient_renk2': kd.get('arkaplan_gradient_renk2'),
                    'arkaplan_gradient_yon': kd.get('arkaplan_gradient_yon'),
                    'id': d['id'],
                })
            else:
                # Kaynak silinmiş — sadece bağlantıyı temizle, mevcut (boş) veri kalır.
                conn.execute(
                    'UPDATE sayfalar SET sablon_kaynak_cihaz_id = NULL, sablon_kaynak_sayfa_ad = NULL WHERE id = ?',
                    (d['id'],)
                )
        conn.commit()
        return True
    finally:
        conn.close()
