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
import math
import random
import threading
import time
from datetime import datetime

DB_NAME = 'oventechiot.db'


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
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

def cihaz_ekle(proje_id: int, ad: str):
    """cihaz_kimlik otomatik üretilir (ESP32 firmware'ine bu değer girilecek)."""
    cihaz_kimlik = secrets.token_hex(12)
    conn = get_db()
    try:
        cur = conn.execute('''
            INSERT INTO cihazlar (proje_id, cihaz_kimlik, ad) VALUES (?, ?, ?)
        ''', (proje_id, cihaz_kimlik, ad.strip()))
        conn.commit()
        return True, {'id': cur.lastrowid, 'cihaz_kimlik': cihaz_kimlik}
    except sqlite3.IntegrityError:
        return False, ('Bu proje artık bulunamıyor (oturumunuz güncel olmayan bir '
                        'projeye işaret ediyor olabilir). Çıkış yapıp tekrar giriş deneyin.')
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


# ============================================================
# TAG
# ============================================================

def tag_ekle(cihaz_id: int, ad: str, modbus_adres: str, veri_tipi: str = 'bool', erisim: str = 'read'):
    conn = get_db()
    try:
        cur = conn.execute('''
            INSERT INTO tagler (cihaz_id, ad, modbus_adres, veri_tipi, erisim)
            VALUES (?, ?, ?, ?, ?)
        ''', (cihaz_id, ad.strip(), modbus_adres.strip(), veri_tipi, erisim))
        conn.commit()
        return True, cur.lastrowid
    except sqlite3.IntegrityError:
        return False, 'Bu isimde bir tag bu cihazda zaten var'
    finally:
        conn.close()


def tag_guncelle(tag_id: int, ad: str, modbus_adres: str, veri_tipi: str, erisim: str):
    conn = get_db()
    try:
        conn.execute('''
            UPDATE tagler SET ad = ?, modbus_adres = ?, veri_tipi = ?, erisim = ?
            WHERE id = ?
        ''', (ad.strip(), modbus_adres.strip(), veri_tipi, erisim, tag_id))
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


# Kullanıcı isteği: grafik geçmişine her canlı güncellemede değil, en fazla
# şu aralıkla bir satır düşsün (10-30sn aralığı istendi, ortası seçildi) —
# ESP32/simülatör çok daha sık senkron olsa bile veritabanı şişmesin.
GECMIS_MIN_ARALIK_SN = 20


def tag_deger_guncelle(tag_id: int, deger):
    """ESP32'den gelen okunan değeri kaydeder (xchange sırasında kullanılacak).
    Aynı zamanda grafik elementinin okuyabilmesi için bir geçmiş satırı da
    ekler (tag_deger_gecmis) — ama en fazla GECMIS_MIN_ARALIK_SN'de bir kez;
    her tag için son GECMIS_MAKS_SATIR satır tutulur."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE tagler SET deger = ?, deger_zamani = datetime('now', '+3 hours') WHERE id = ?",
            (str(deger), tag_id)
        )
        son = conn.execute(
            "SELECT zaman FROM tag_deger_gecmis WHERE tag_id = ? ORDER BY id DESC LIMIT 1",
            (tag_id,)
        ).fetchone()
        yeterince_eski = (son is None) or conn.execute(
            "SELECT (julianday(datetime('now', '+3 hours')) - julianday(?)) * 86400 >= ?",
            (son['zaman'], GECMIS_MIN_ARALIK_SN)
        ).fetchone()[0]
        if yeterince_eski:
            conn.execute(
                "INSERT INTO tag_deger_gecmis (tag_id, deger) VALUES (?, ?)",
                (tag_id, str(deger))
            )
        GECMIS_MAKS_SATIR = 500
        conn.execute('''
            DELETE FROM tag_deger_gecmis WHERE tag_id = ? AND id NOT IN (
                SELECT id FROM tag_deger_gecmis WHERE tag_id = ? ORDER BY id DESC LIMIT ?
            )
        ''', (tag_id, tag_id, GECMIS_MAKS_SATIR))
        conn.commit()
    finally:
        conn.close()
    # Alarm değerlendirmesi ayrı bir bağlantıyla (yukarıdaki commit'ten
    # SONRA) yapılıyor — tarayıcı hiç açık olmasa bile burada tetiklenir.
    alarm_degerlendir(tag_id, deger)
    return True


def tag_deger_gecmisi(tag_id: int, limit: int = 100):
    """Bir tag'in en son `limit` geçmiş değerini ESKİDEN YENİYE sıralı döner:
    [{deger, zaman}, ...] — grafik elementi bunu çizer."""
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


def demo_veri_olustur():
    """Uygulama her başlatıldığında (app.py -> init_db() sonrası) çağrılır.
    Render'ın diski her deploy'da sıfırlanıyor, .db dosyası da onunla
    birlikte gidiyor — kullanıcı her seferinde elle test projesi/cihaz/
    kullanıcı/sayfa oluşturmak zorunda kalmasın diye örnek bir demo proje
    otomatik oluşturulur/tazelenir. Demo verisi gerçek müşteri verisi
    DEĞİLDİR — bu yüzden her başlangıçta tag'ler + sayfalar (var olsa bile)
    güncel şablona göre yeniden yazılır (sayfa_kaydet zaten UPSERT), böylece
    kod içindeki demo tasarımı güncellenince canlıdaki demo da tazelenir."""
    DEMO_SIFRE = '123456'
    mevcut_proje = proje_getir_kod('demo')
    if mevcut_proje:
        proje_id = mevcut_proje['id']
        conn = get_db()
        try:
            # Kod içindeki şifre değişse bile canlıda zaten oluşmuş demo
            # hesabı eski şifrede takılı kalmasın; rol de OPERATÖR'e senkron
            # tutulur (giriş yapınca doğrudan tam ekran/kart deneyimi).
            conn.execute('UPDATE kullanicilar SET sifre_hash = ? WHERE kullanici_adi = ?',
                         (_sifre_hashle(DEMO_SIFRE), 'demo'))
            conn.execute("UPDATE kullanicilar SET rol = 'operator' WHERE kullanici_adi = ?", ('demo',))
            conn.commit()
        finally:
            conn.close()
    else:
        ok, proje_id = proje_ekle('demo', 'Demo Proje (Test / Şablon)')
        if not ok:
            return
        kullanici_ekle(proje_id, 'demo', DEMO_SIFRE, 'Demo Kullanıcı', rol='operator')

    cihazlar = proje_cihazlari(proje_id)
    cihaz = next((c for c in cihazlar if c['ad'] == 'Demo Fırın'), None)
    if not cihaz:
        ok, cihaz_bilgi = cihaz_ekle(proje_id, 'Demo Fırın')
        if not ok:
            return
        cihaz_id = cihaz_bilgi['id']
    else:
        cihaz_id = cihaz['id']

    # Eski (tek sayfalık) demo sürümünden kalan artık sayfayı temizle —
    # yeni şablon 5 ayrı sayfa kullanıyor, "Şablon - Alt Menü" artık yok.
    sayfa_sil(cihaz_id, 'Şablon - Alt Menü')
    if not cihaz or not cihaz.get('baslangic_sayfa'):
        cihaz_baslangic_sayfa_guncelle(cihaz_id, 'Ana Sayfa')

    tag_idler = _demo_tagleri_olustur(cihaz_id)
    _demo_sayfalari_olustur(cihaz_id, tag_idler)
    demo_simulator_baslat()


def _demo_tag_al(cihaz_id: int, ad: str, modbus_adres: str, veri_tipi: str, erisim: str) -> int:
    """tag_ekle idempotent değil (aynı isimde ikinci çağrı hata döner) —
    demo her başlangıçta yeniden kurulduğu için burada 'yoksa ekle, varsa
    id'sini getir' sarmalayıcısı kullanılıyor."""
    ok, sonuc = tag_ekle(cihaz_id, ad, modbus_adres, veri_tipi, erisim)
    if ok:
        return sonuc
    for t in cihaz_tagleri(cihaz_id):
        if t['ad'] == ad:
            return t['id']
    return None


def _demo_tagleri_olustur(cihaz_id: int) -> dict:
    """Demo fırın için gerçekçi bir tag seti — okuma (sensör) VE
    okuma+yazma (kullanıcı kontrolü) tag'lerinin karışımı, farklı veri
    tiplerini de (bool/int/float) gösterecek şekilde."""
    return {
        'isitici':        _demo_tag_al(cihaz_id, 'Isıtıcı', '0', 'bool', 'readwrite'),
        'sicaklik':       _demo_tag_al(cihaz_id, 'Sıcaklık', '1', 'float', 'read'),
        'hedef_sicaklik': _demo_tag_al(cihaz_id, 'Hedef Sıcaklık', '2', 'float', 'readwrite'),
        'nem':            _demo_tag_al(cihaz_id, 'Nem', '3', 'float', 'read'),
        'basinc':         _demo_tag_al(cihaz_id, 'Basınç', '4', 'float', 'read'),
        'alarm':          _demo_tag_al(cihaz_id, 'Alarm', '5', 'bool', 'read'),
        'konveyor_hiz':    _demo_tag_al(cihaz_id, 'Konveyör Hız', '6', 'float', 'readwrite'),
        'konveyor_calisiyor': _demo_tag_al(cihaz_id, 'Konveyör Çalışıyor', '7', 'bool', 'readwrite'),
        'calisma_modu':   _demo_tag_al(cihaz_id, 'Çalışma Modu', '8', 'int', 'readwrite'),
        'alarm_bildirim': _demo_tag_al(cihaz_id, 'Alarm Bildirimi', '9', 'bool', 'readwrite'),
    }


# ============================================================
# DEMO ŞABLONU — mobil, 5 sayfa + alt navbar (gerçekten çalışan
# "Sayfaya Git" sekmeleri) + her sayfada farklı giriş animasyonu.
# ============================================================

DEMO_NAVBAR_SEKMELERI = [
    ('🏠', 'Ana Sayfa'),
    ('🔥', 'Fırın'),
    ('📊', 'Grafik'),
    ('🎚️', 'Konveyör'),
    ('⚙️', 'Ayarlar'),
]


def _demo_navbar_olustur(aktif_sayfa_ad: str):
    """5 sekmeli, GERÇEKTEN çalışan alt navbar — her sekme kendi 'Sayfaya
    Git' butonu, aktif sekme parlak/kalın + arkasında vurgu (pill) ile
    gösterilir. Kaydırma sırasında ekranda sabit kalması için tüm
    elementler sabit_konum='alt' taşır (bkz. runtime #tuval-sabit-alt)."""
    def _id():
        return 'el_' + secrets.token_hex(4)

    n = len(DEMO_NAVBAR_SEKMELERI)
    kenar, aralik, toplam_w = 4, 4, 420
    tab_w = (toplam_w - 2 * kenar - (n - 1) * aralik) / n

    elementler = [
        {
            'id': _id(), 'type': 'sekil', 'x': 0, 'y': 796, 'w': 420, 'h': 64,
            'sekil_dolgu': '#17222e', 'sekil_kenarlik': '#2a3b4c', 'sekil_kenarlik_kalinlik': 1,
            'sekil_kose_solust': 20, 'sekil_kose_sagust': 20, 'sekil_kose_solalt': 0, 'sekil_kose_sagalt': 0,
            'sekil_gradient_aktif': True, 'sekil_gradient_renk1': '#1e2d3d', 'sekil_gradient_renk2': '#0f1720',
            'sekil_gradient_yon': 'to bottom', 'custom_css': 'box-shadow: 0 -4px 20px rgba(0,0,0,0.35);',
            'sabit_konum': 'alt',
        },
    ]
    for i, (ikon, hedef) in enumerate(DEMO_NAVBAR_SEKMELERI):
        x = round(kenar + i * (tab_w + aralik), 1)
        aktif = (hedef == aktif_sayfa_ad)
        if aktif:
            elementler.append({
                'id': _id(), 'type': 'sekil', 'x': round(x - 3, 1), 'y': 800, 'w': round(tab_w + 6, 1), 'h': 50,
                'sekil_dolgu': '#2e9ed933', 'sekil_kenarlik': 'transparent', 'sekil_kenarlik_kalinlik': 0,
                'sekil_kose_solust': 14, 'sekil_kose_sagust': 14, 'sekil_kose_solalt': 14, 'sekil_kose_sagalt': 14,
                'sekil_gradient_aktif': False, 'sekil_gradient_renk1': '#2e9ed9', 'sekil_gradient_renk2': '#1f7bb0',
                'sekil_gradient_yon': 'to bottom', 'custom_css': '', 'sabit_konum': 'alt',
            })
        elementler.append({
            'id': _id(), 'type': 'button', 'x': x, 'y': 806, 'w': round(tab_w, 1), 'h': 46,
            'label': f'{ikon}\n{hedef}', 'tag_id': None, 'mod': 'sayfaya_git', 'hedef_sayfa': hedef,
            'acik_deger': '1', 'kapali_deger': '0',
            'renk_acik': 'transparent', 'renk_kapali': 'transparent',
            'renk_yazi': '#ffffff' if aktif else '#8aa0b3',
            'resim_url_acik': '', 'resim_url_kapali': '',
            'custom_css': 'white-space:pre-line;line-height:1.4;font-size:10px;box-shadow:none;padding:0 2px;'
                          + ('font-weight:700;' if aktif else 'font-weight:600;'),
            'sabit_konum': 'alt',
        })
    return elementler


def _demo_baslik(baslik: str, alt_baslik: str = ''):
    els = [{
        'id': 'el_' + secrets.token_hex(4), 'type': 'label',
        'x': 20, 'y': 24, 'w': 380, 'h': 32,
        'label': baslik, 'tag_id': None, 'renk_yazi': '#e8eef4',
        'renk_arkaplan': 'transparent', 'font_boyutu': 21, 'custom_css': 'font-weight:700;'
    }]
    if alt_baslik:
        els.append({
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 20, 'y': 56, 'w': 380, 'h': 22,
            'label': alt_baslik, 'tag_id': None, 'renk_yazi': '#7f93a8',
            'renk_arkaplan': 'transparent', 'font_boyutu': 12, 'custom_css': ''
        })
    return els


def _demo_sayfalari_olustur(cihaz_id: int, tid: dict):
    ORTAK = dict(hedef='mobil', tuval_w=420, tuval_h=860,
                 arkaplan_gradient_aktif=True, arkaplan_gradient_renk1='#1e2d3d',
                 arkaplan_gradient_renk2='#0f1720', arkaplan_gradient_yon='to bottom')

    # ---------------- Ana Sayfa ----------------
    ana = _demo_baslik('Demo Fırın', 'Canlı İzleme Paneli') + [
        {  # Alarm durum şeridi
            'id': 'el_' + secrets.token_hex(4), 'type': 'durum',
            'x': 20, 'y': 90, 'w': 380, 'h': 40,
            'tag_id': tid['alarm'], 'durum_modu': 'metin', 'durum_onizleme_index': 0,
            'durumlar': [
                {'deger': '0', 'metin': '✅ Sistem Normal', 'renk_arkaplan': '#1c3326', 'renk_yazi': '#2ecc71', 'font_boyutu': 13},
                {'deger': '1', 'metin': '⚠️ Alarm Aktif', 'renk_arkaplan': '#3a1f22', 'renk_yazi': '#e74c3c', 'font_boyutu': 13},
            ],
            'custom_css': 'border-radius:8px;font-weight:700;'
        },
        {  # Sıcaklık kartı
            'id': 'el_' + secrets.token_hex(4), 'type': 'groupbox',
            'x': 20, 'y': 146, 'w': 182, 'h': 100,
            'baslik': '🌡️ Sıcaklık', 'renk_yazi': '#8aa0b3', 'font_boyutu': 11,
            'renk_kenarlik': '#2a3b4c', 'kenarlik_kalinlik': 1,
            'renk_arkaplan': '#17222e', 'kose_yaricapi': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'textbox',
            'x': 32, 'y': 178, 'w': 158, 'h': 50,
            'tag_id': tid['sicaklik'], 'on_ek': '', 'son_ek': ' °C', 'ondalik': 1, 'font_boyutu': 26,
            'renk_arkaplan': 'transparent', 'renk_yazi': '#2e9ed9', 'renk_kenarlik': 'transparent',
            'custom_css': 'font-weight:700;justify-content:flex-start;'
        },
        {  # Nem kartı
            'id': 'el_' + secrets.token_hex(4), 'type': 'groupbox',
            'x': 218, 'y': 146, 'w': 182, 'h': 100,
            'baslik': '💧 Nem', 'renk_yazi': '#8aa0b3', 'font_boyutu': 11,
            'renk_kenarlik': '#2a3b4c', 'kenarlik_kalinlik': 1,
            'renk_arkaplan': '#17222e', 'kose_yaricapi': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'textbox',
            'x': 230, 'y': 178, 'w': 158, 'h': 50,
            'tag_id': tid['nem'], 'on_ek': '', 'son_ek': ' %', 'ondalik': 0, 'font_boyutu': 26,
            'renk_arkaplan': 'transparent', 'renk_yazi': '#2ecc71', 'renk_kenarlik': 'transparent',
            'custom_css': 'font-weight:700;justify-content:flex-start;'
        },
        {  # Isıtıcı hızlı kontrol
            'id': 'el_' + secrets.token_hex(4), 'type': 'groupbox',
            'x': 20, 'y': 262, 'w': 380, 'h': 74,
            'baslik': '', 'renk_yazi': '#8aa0b3', 'font_boyutu': 11,
            'renk_kenarlik': '#2a3b4c', 'kenarlik_kalinlik': 1,
            'renk_arkaplan': '#17222e', 'kose_yaricapi': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 40, 'y': 286, 'w': 200, 'h': 26,
            'label': '🔥  Isıtıcı', 'tag_id': None, 'renk_yazi': '#e8eef4',
            'renk_arkaplan': 'transparent', 'font_boyutu': 15, 'custom_css': 'font-weight:600;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'switch',
            'x': 320, 'y': 286, 'w': 60, 'h': 26,
            'tag_id': tid['isitici'], 'acik_deger': '1', 'kapali_deger': '0',
            'metin_acik': 'ON', 'metin_kapali': 'OFF',
            'renk_acik': '#2ecc71', 'renk_kapali': '#3a4a5c', 'daire_rengi': '#ffffff', 'font_boyutu': 9,
            'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 20, 'y': 352, 'w': 380, 'h': 60,
            'label': 'Alt menüden Fırın, Grafik, Konveyör ve Ayarlar sayfalarına geçebilirsin — her biri kendi kayma efektiyle açılır.',
            'tag_id': None, 'renk_yazi': '#7f93a8', 'renk_arkaplan': 'transparent', 'font_boyutu': 12,
            'custom_css': 'white-space:normal;line-height:1.5;'
        },
    ] + _demo_navbar_olustur('Ana Sayfa')
    sayfa_kaydet(cihaz_id, 'Ana Sayfa', ana, arkaplan='#1e2d3d', sayfa_turu='normal',
                 giris_animasyonu='fade', **ORTAK)

    # ---------------- Fırın ----------------
    firin = _demo_baslik('Fırın Kontrolü', 'Sıcaklık ve ısıtıcı yönetimi') + [
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'durum',
            'x': 20, 'y': 90, 'w': 380, 'h': 40,
            'tag_id': tid['isitici'], 'durum_modu': 'metin', 'durum_onizleme_index': 1,
            'durumlar': [
                {'deger': '0', 'metin': '⏸️ Duruyor', 'renk_arkaplan': '#2a2f38', 'renk_yazi': '#9fb3c8', 'font_boyutu': 13},
                {'deger': '1', 'metin': '🔥 Isıtıyor', 'renk_arkaplan': '#3a2c1c', 'renk_yazi': '#f39c12', 'font_boyutu': 13},
            ],
            'custom_css': 'border-radius:8px;font-weight:700;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 20, 'y': 148, 'w': 200, 'h': 20,
            'label': 'Fırın Sıcaklığı', 'tag_id': None, 'renk_yazi': '#8aa0b3',
            'renk_arkaplan': 'transparent', 'font_boyutu': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'progressbar',
            'x': 20, 'y': 172, 'w': 380, 'h': 34,
            'tag_id': tid['sicaklik'], 'min_deger': 0, 'max_deger': 250, 'yon': 'yatay',
            'renk_dolgu': '#e74c3c', 'renk_arkaplan': '#17222e', 'birim': ' °C',
            'deger_goster': True, 'custom_css': 'border-radius:8px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 20, 'y': 226, 'w': 200, 'h': 20,
            'label': 'Hedef Sıcaklık (°C)', 'tag_id': None, 'renk_yazi': '#8aa0b3',
            'renk_arkaplan': 'transparent', 'font_boyutu': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'inputbox',
            'x': 20, 'y': 250, 'w': 180, 'h': 40,
            'tag_id': tid['hedef_sicaklik'], 'giris_turu': 'sayi', 'placeholder': 'örn. 180', 'font_boyutu': 16,
            'renk_arkaplan': '#17222e', 'renk_yazi': '#e8eef4', 'renk_kenarlik': '#2e9ed9',
            'custom_css': 'border-radius:8px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 220, 'y': 226, 'w': 180, 'h': 20,
            'label': 'Isıtıcı', 'tag_id': None, 'renk_yazi': '#8aa0b3',
            'renk_arkaplan': 'transparent', 'font_boyutu': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'button',
            'x': 220, 'y': 250, 'w': 180, 'h': 40,
            'label': 'Isıtıcı Aç/Kapa', 'tag_id': tid['isitici'], 'mod': 'toggle', 'hedef_sayfa': '',
            'acik_deger': '1', 'kapali_deger': '0',
            'renk_acik': '#2ecc71', 'renk_kapali': '#e74c3c', 'renk_yazi': '#ffffff',
            'resim_url_acik': '', 'resim_url_kapali': '', 'custom_css': 'border-radius:8px;font-size:13px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'grafik',
            'x': 20, 'y': 308, 'w': 380, 'h': 130,
            'baslik': 'Son 20 Ölçüm', 'birim': '°C', 'nokta_sayisi': 20, 'arkaplan_rengi': '#17222e',
            'seriler': [{'tag_id': tid['sicaklik'], 'etiket': 'Sıcaklık', 'renk': '#e74c3c'}],
            'custom_css': 'border-radius:10px;'
        },
    ] + _demo_navbar_olustur('Fırın')
    sayfa_kaydet(cihaz_id, 'Fırın', firin, arkaplan='#1e2d3d', sayfa_turu='normal',
                 giris_animasyonu='soldan-saga', **ORTAK)

    # ---------------- Grafik ----------------
    grafik = _demo_baslik('Canlı Grafik', 'Sıcaklık · Nem · Basınç') + [
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'grafik',
            'x': 20, 'y': 90, 'w': 380, 'h': 220,
            'baslik': 'Ortam Verileri', 'birim': '', 'nokta_sayisi': 40, 'arkaplan_rengi': '#17222e',
            'seriler': [
                {'tag_id': tid['sicaklik'], 'etiket': 'Sıcaklık °C', 'renk': '#e74c3c'},
                {'tag_id': tid['nem'], 'etiket': 'Nem %', 'renk': '#2ecc71'},
                {'tag_id': tid['basinc'], 'etiket': 'Basınç bar', 'renk': '#f39c12'},
            ],
            'custom_css': 'border-radius:10px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'grafik',
            'x': 20, 'y': 326, 'w': 380, 'h': 140,
            'baslik': 'Konveyör Hızı', 'birim': ' m/dk', 'nokta_sayisi': 40, 'arkaplan_rengi': '#17222e',
            'seriler': [{'tag_id': tid['konveyor_hiz'], 'etiket': 'Hız', 'renk': '#2e9ed9'}],
            'custom_css': 'border-radius:10px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 20, 'y': 480, 'w': 380, 'h': 44,
            'label': 'Grafikler gerçek geçmiş veriyi gösterir — sayfayı kapatıp tekrar açsan da veriler kalıcıdır.',
            'tag_id': None, 'renk_yazi': '#7f93a8', 'renk_arkaplan': 'transparent', 'font_boyutu': 11,
            'custom_css': 'white-space:normal;line-height:1.5;'
        },
    ] + _demo_navbar_olustur('Grafik')
    sayfa_kaydet(cihaz_id, 'Grafik', grafik, arkaplan='#1e2d3d', sayfa_turu='normal',
                 giris_animasyonu='sagdan-sola', **ORTAK)

    # ---------------- Konveyör ----------------
    konveyor = _demo_baslik('Konveyör Kontrolü', 'Hız ve çalışma durumu') + [
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'durum',
            'x': 20, 'y': 90, 'w': 380, 'h': 40,
            'tag_id': tid['konveyor_calisiyor'], 'durum_modu': 'metin', 'durum_onizleme_index': 1,
            'durumlar': [
                {'deger': '0', 'metin': '⏹️ Durdu', 'renk_arkaplan': '#2a2f38', 'renk_yazi': '#9fb3c8', 'font_boyutu': 13},
                {'deger': '1', 'metin': '▶️ Çalışıyor', 'renk_arkaplan': '#1c3326', 'renk_yazi': '#2ecc71', 'font_boyutu': 13},
            ],
            'custom_css': 'border-radius:8px;font-weight:700;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 20, 'y': 148, 'w': 200, 'h': 20,
            'label': 'Anlık Hız', 'tag_id': None, 'renk_yazi': '#8aa0b3',
            'renk_arkaplan': 'transparent', 'font_boyutu': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'progressbar',
            'x': 20, 'y': 172, 'w': 380, 'h': 34,
            'tag_id': tid['konveyor_hiz'], 'min_deger': 0, 'max_deger': 60, 'yon': 'yatay',
            'renk_dolgu': '#2e9ed9', 'renk_arkaplan': '#17222e', 'birim': ' m/dk',
            'deger_goster': True, 'custom_css': 'border-radius:8px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 20, 'y': 226, 'w': 200, 'h': 20,
            'label': 'Hız Ayarı (m/dk)', 'tag_id': None, 'renk_yazi': '#8aa0b3',
            'renk_arkaplan': 'transparent', 'font_boyutu': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'inputbox',
            'x': 20, 'y': 250, 'w': 180, 'h': 40,
            'tag_id': tid['konveyor_hiz'], 'giris_turu': 'sayi', 'placeholder': 'örn. 30', 'font_boyutu': 16,
            'renk_arkaplan': '#17222e', 'renk_yazi': '#e8eef4', 'renk_kenarlik': '#2e9ed9',
            'custom_css': 'border-radius:8px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 220, 'y': 226, 'w': 180, 'h': 20,
            'label': 'Motor', 'tag_id': None, 'renk_yazi': '#8aa0b3',
            'renk_arkaplan': 'transparent', 'font_boyutu': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'switch',
            'x': 220, 'y': 254, 'w': 70, 'h': 30,
            'tag_id': tid['konveyor_calisiyor'], 'acik_deger': '1', 'kapali_deger': '0',
            'metin_acik': 'ÇALIŞ', 'metin_kapali': 'DUR',
            'renk_acik': '#2ecc71', 'renk_kapali': '#3a4a5c', 'daire_rengi': '#ffffff', 'font_boyutu': 9,
            'custom_css': ''
        },
    ] + _demo_navbar_olustur('Konveyör')
    sayfa_kaydet(cihaz_id, 'Konveyör', konveyor, arkaplan='#1e2d3d', sayfa_turu='normal',
                 giris_animasyonu='yukaridan-asagi', **ORTAK)

    # ---------------- Ayarlar ----------------
    ayarlar = _demo_baslik('Ayarlar', 'Fırın davranışını özelleştir') + [
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'groupbox',
            'x': 20, 'y': 90, 'w': 380, 'h': 110,
            'baslik': 'Sıcaklık Hedefi', 'renk_yazi': '#8aa0b3', 'font_boyutu': 12,
            'renk_kenarlik': '#2a3b4c', 'kenarlik_kalinlik': 1,
            'renk_arkaplan': '#17222e', 'kose_yaricapi': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 40, 'y': 122, 'w': 200, 'h': 20,
            'label': 'Hedef Sıcaklık (°C)', 'tag_id': None, 'renk_yazi': '#e8eef4',
            'renk_arkaplan': 'transparent', 'font_boyutu': 13, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'inputbox',
            'x': 40, 'y': 148, 'w': 340, 'h': 40,
            'tag_id': tid['hedef_sicaklik'], 'giris_turu': 'sayi', 'placeholder': 'örn. 180', 'font_boyutu': 16,
            'renk_arkaplan': '#0f1720', 'renk_yazi': '#e8eef4', 'renk_kenarlik': '#2e9ed9',
            'custom_css': 'border-radius:8px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'groupbox',
            'x': 20, 'y': 214, 'w': 380, 'h': 110,
            'baslik': 'Çalışma Modu', 'renk_yazi': '#8aa0b3', 'font_boyutu': 12,
            'renk_kenarlik': '#2a3b4c', 'kenarlik_kalinlik': 1,
            'renk_arkaplan': '#17222e', 'kose_yaricapi': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'label',
            'x': 40, 'y': 246, 'w': 200, 'h': 20,
            'label': 'Fırın Modu', 'tag_id': None, 'renk_yazi': '#e8eef4',
            'renk_arkaplan': 'transparent', 'font_boyutu': 13, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'combobox',
            'x': 40, 'y': 272, 'w': 340, 'h': 40,
            'tag_id': tid['calisma_modu'], 'font_boyutu': 14,
            'secenekler': [
                {'deger': '0', 'etiket': 'Kapalı'},
                {'deger': '1', 'etiket': 'Otomatik'},
                {'deger': '2', 'etiket': 'Manuel'},
            ],
            'custom_css': 'border-radius:8px;'
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'groupbox',
            'x': 20, 'y': 338, 'w': 380, 'h': 66,
            'baslik': '', 'renk_yazi': '#8aa0b3', 'font_boyutu': 12,
            'renk_kenarlik': '#2a3b4c', 'kenarlik_kalinlik': 1,
            'renk_arkaplan': '#17222e', 'kose_yaricapi': 12, 'custom_css': ''
        },
        {
            'id': 'el_' + secrets.token_hex(4), 'type': 'checkbox',
            'x': 40, 'y': 362, 'w': 340, 'h': 24,
            'tag_id': tid['alarm_bildirim'], 'acik_deger': '1', 'kapali_deger': '0',
            'metin': '🔔  Alarm Bildirimlerini Aç', 'renk_yazi': '#e8eef4', 'renk_acik': '#2e9ed9',
            'font_boyutu': 14, 'custom_css': ''
        },
    ] + _demo_navbar_olustur('Ayarlar')
    sayfa_kaydet(cihaz_id, 'Ayarlar', ayarlar, arkaplan='#1e2d3d', sayfa_turu='normal',
                 giris_animasyonu='asagidan-yukari', **ORTAK)


# ============================================================
# DEMO SİMÜLATÖRÜ — gerçek bir ESP32/PLC bağlı değilken demo tag'lerine
# canlı, gerçekçi değerler üretir; grafik/durum/progressbar elementleri
# boş görünmesin ve tag_deger_gecmis gerçekten dolsun diye. Kullanıcının
# yazdığı (readwrite) değerleri de "cihaz uyguladı" gibi anında geri
# yansıtır (yazilacak_deger -> deger) — böylece switch/inputbox/combobox
# gerçekten TEPKİ VEREN bir demo gibi çalışır.
# ============================================================

_demo_simulator_calisiyor = False


def demo_simulator_baslat():
    global _demo_simulator_calisiyor
    if _demo_simulator_calisiyor:
        return
    _demo_simulator_calisiyor = True

    def _dongu():
        t = 0
        while True:
            try:
                proje = proje_getir_kod('demo')
                if proje:
                    for c in proje_cihazlari(proje['id']):
                        for tag in cihaz_tagleri(c['id']):
                            yeni_deger = _demo_tag_simule_et(tag, t)
                            if yeni_deger is not None:
                                tag_deger_guncelle(tag['id'], yeni_deger)
            except Exception:
                pass  # simülatör asla uygulamayı düşürmesin
            t += 1
            time.sleep(4)

    th = threading.Thread(target=_dongu, name='demo-simulator', daemon=True)
    th.start()


def _demo_tag_simule_et(tag: dict, t: int):
    """Bir tag için bu tick'te yazılacak yeni değeri döner (değişiklik
    yoksa None). readwrite/write tag'lerde kullanıcının en son yazdığı
    (yazilacak_deger) varsa onu 'cihaz uyguladı' gibi deger'e yansıtır —
    aksi halde tag adına göre gerçekçi bir dalga/rastgelelik üretir."""
    ad = tag['ad']
    if tag['erisim'] in ('write', 'readwrite') and tag.get('yazilacak_deger') not in (None, ''):
        if str(tag.get('deger')) != str(tag['yazilacak_deger']):
            return tag['yazilacak_deger']
        return None
    if ad == 'Sıcaklık':
        return round(120 + 60 * math.sin(t / 9) + random.uniform(-2, 2), 1)
    if ad == 'Nem':
        return round(45 + 12 * math.sin(t / 7 + 1) + random.uniform(-1, 1), 1)
    if ad == 'Basınç':
        return round(1.0 + 0.15 * math.sin(t / 11 + 2) + random.uniform(-0.02, 0.02), 2)
    if ad == 'Alarm':
        return '1' if random.random() < 0.015 else '0'
    if ad == 'Isıtıcı' and tag.get('deger') is None:
        return '0'
    if ad == 'Konveyör Çalışıyor' and tag.get('deger') is None:
        return '0'
    if ad == 'Konveyör Hız' and tag.get('deger') is None:
        return '0'
    return None


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
