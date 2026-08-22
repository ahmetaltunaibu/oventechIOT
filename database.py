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


def tag_deger_guncelle(tag_id: int, deger):
    """ESP32'den gelen okunan değeri kaydeder (xchange sırasında kullanılacak)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE tagler SET deger = ?, deger_zamani = datetime('now', '+3 hours') WHERE id = ?",
            (str(deger), tag_id)
        )
        conn.commit()
        return True
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
    otomatik oluşturulur. Zaten varsa (kod='demo') hiçbir şeye dokunmaz,
    yani var olan gerçek veriler asla ezilmez — bu SADECE eksikse eklenen
    ayrı bir örnek projedir, gerçek verinin yerine geçmez."""
    DEMO_SIFRE = '123456'
    mevcut_proje = proje_getir_kod('demo')
    if mevcut_proje:
        # Proje zaten var — yine de demo kullanıcının şifresini güncel
        # varsayılana senkron tut (kod içindeki şifre değişse bile canlıda
        # zaten oluşmuş demo hesabı eski şifrede takılı kalmasın).
        conn = get_db()
        try:
            conn.execute(
                'UPDATE kullanicilar SET sifre_hash = ? WHERE kullanici_adi = ?',
                (_sifre_hashle(DEMO_SIFRE), 'demo')
            )
            conn.commit()
        finally:
            conn.close()
        return

    ok, proje_id = proje_ekle('demo', 'Demo Proje (Test / Şablon)')
    if not ok:
        return

    kullanici_ekle(proje_id, 'demo', DEMO_SIFRE, 'Demo Kullanıcı', rol='tasarimci')

    ok, cihaz_bilgi = cihaz_ekle(proje_id, 'Demo Fırın')
    if not ok:
        return
    cihaz_id = cihaz_bilgi['id']

    _, isitici_tag_id = tag_ekle(cihaz_id, 'Isıtıcı', '0', 'bool', 'readwrite')
    tag_ekle(cihaz_id, 'Sıcaklık', '1', 'float', 'read')

    elementler = [
        {
            'id': 'el_demo_baslik', 'type': 'label',
            'x': 40, 'y': 30, 'w': 340, 'h': 40,
            'label': 'Demo Fırın Kontrol Paneli', 'renk_yazi': '#e8eef4',
            'renk_arkaplan': 'transparent', 'font_boyutu': 22, 'custom_css': ''
        },
        {
            'id': 'el_demo_panel', 'type': 'sekil',
            'x': 40, 'y': 90, 'w': 380, 'h': 220,
            'sekil_dolgu': '#22384d', 'sekil_kenarlik': '#2e9ed9', 'sekil_kenarlik_kalinlik': 2,
            'sekil_kose_solust': 12, 'sekil_kose_sagust': 12, 'sekil_kose_solalt': 12, 'sekil_kose_sagalt': 12,
            'sekil_gradient_aktif': False, 'sekil_gradient_renk1': '#2e9ed9', 'sekil_gradient_renk2': '#8e44ad',
            'sekil_gradient_yon': 'to right', 'custom_css': ''
        },
        {
            'id': 'el_demo_buton', 'type': 'button',
            'x': 70, 'y': 130, 'w': 140, 'h': 48,
            'label': 'Isıtıcı', 'tag_id': isitici_tag_id, 'mod': 'toggle',
            'acik_deger': '1', 'kapali_deger': '0',
            'renk_acik': '#2ecc71', 'renk_kapali': '#e74c3c', 'renk_yazi': '#ffffff', 'custom_css': ''
        },
        {
            'id': 'el_demo_not', 'type': 'label',
            'x': 70, 'y': 200, 'w': 300, 'h': 60,
            'label': "Bu sayfa örnek/şablon olarak eklendi. Sağ tık > Şablon Uygula ile kendi sayfalarına kopyalayabilirsin.",
            'renk_yazi': '#9fb3c8', 'renk_arkaplan': 'transparent', 'font_boyutu': 13, 'custom_css': ''
        },
    ]
    sayfa_kaydet(cihaz_id, 'Ana Sayfa', elementler, hedef='masaustu', tuval_w=1280, tuval_h=800, arkaplan='#1e2d3d')

    # Kullanıcı isteği: gemba'daki mobil giriş ekranının alt navbar + "+"
    # hızlı-erişim (speed dial) düzeninden esinlenerek, oventechIOT'un kendi
    # renk paletiyle (koyu lacivert + mavi accent) yeniden üretilmiş, hazır
    # bir MOBİL navigasyon şablonu. Ayrı bir "Şablon" sayfası olarak
    # eklenir (kaynak — burada düzenlenebilir) VE Ana Sayfa'nın mobil
    # düzenine de "Şablon Uygula" yapılmış GİBİ (kilitli, sablon_kaynagi
    # işaretli) varsayılan olarak YANSITILIR — kullanıcı elle uygulamadan
    # da örnek kullanımı görsün. Web (masaüstü) için şimdilik eklenmiyor.
    # Şablon sayfasının KENDİSİ (sadece orada görünür, başka sayfalara
    # yansıtılmaz) — ne olduğu tek bakışta anlaşılsın diye başlık, açıklama
    # ve bir örnek içerik kartı ile "profesyonel" bir mockup gibi sunulur.
    sablon_vitrin_elementleri = [
        {
            'id': 'el_sablon_baslik', 'type': 'label',
            'x': 20, 'y': 28, 'w': 380, 'h': 30,
            'label': '📱 Mobil Navigasyon Şablonu', 'tag_id': None, 'renk_yazi': '#e8eef4',
            'renk_arkaplan': 'transparent', 'font_boyutu': 18, 'custom_css': 'font-weight:700;'
        },
        {
            'id': 'el_sablon_aciklama', 'type': 'label',
            'x': 20, 'y': 62, 'w': 380, 'h': 50,
            'label': 'Sağ tık > Şablon Uygula ile diğer sayfalarına ekleyebilirsin. Kaynağı burada düzenle, "🔄 Güncelle" ile her yerde tazele.',
            'tag_id': None, 'renk_yazi': '#8aa0b3', 'renk_arkaplan': 'transparent', 'font_boyutu': 12,
            'custom_css': 'white-space:normal;line-height:1.4;'
        },
        {
            'id': 'el_sablon_kart', 'type': 'sekil',
            'x': 20, 'y': 128, 'w': 380, 'h': 220,
            'sekil_dolgu': '#17222e', 'sekil_kenarlik': '#2a3b4c', 'sekil_kenarlik_kalinlik': 1,
            'sekil_kose_solust': 14, 'sekil_kose_sagust': 14, 'sekil_kose_solalt': 14, 'sekil_kose_sagalt': 14,
            'sekil_gradient_aktif': False, 'sekil_gradient_renk1': '#2e9ed9', 'sekil_gradient_renk2': '#8e44ad',
            'sekil_gradient_yon': 'to right', 'custom_css': ''
        },
        {
            'id': 'el_sablon_kart_ikon', 'type': 'label',
            'x': 20, 'y': 168, 'w': 380, 'h': 60,
            'label': '🧩', 'tag_id': None, 'renk_yazi': '#2e9ed9',
            'renk_arkaplan': 'transparent', 'font_boyutu': 42, 'custom_css': ''
        },
        {
            'id': 'el_sablon_kart_yazi', 'type': 'label',
            'x': 40, 'y': 260, 'w': 340, 'h': 60,
            'label': 'Sayfa içeriğin burada yer alır — bu kart sadece yer tutucudur',
            'tag_id': None, 'renk_yazi': '#8aa0b3', 'renk_arkaplan': 'transparent', 'font_boyutu': 12,
            'custom_css': 'white-space:normal;line-height:1.4;text-align:center;'
        },
    ]
    navbar_sablon_elementleri = sablon_vitrin_elementleri + _mobil_navbar_sablonu()
    sayfa_kaydet(cihaz_id, 'Şablon - Alt Menü', navbar_sablon_elementleri, hedef='mobil',
                 tuval_w=420, tuval_h=860, arkaplan='#0f1720')

    navbar_yansitilan = _mobil_navbar_sablonu(
        sablon_kaynagi=f'{cihaz_id}:Şablon - Alt Menü', kilitli=True
    )
    ana_sayfa_mobil_elementleri = [
        {
            'id': 'el_demo_mobil_baslik', 'type': 'label',
            'x': 20, 'y': 24, 'w': 380, 'h': 36,
            'label': 'Demo Fırın', 'tag_id': None, 'renk_yazi': '#e8eef4',
            'renk_arkaplan': 'transparent', 'font_boyutu': 20, 'custom_css': 'font-weight:700;'
        },
        {
            'id': 'el_demo_mobil_buton', 'type': 'button',
            'x': 20, 'y': 80, 'w': 160, 'h': 52,
            'label': 'Isıtıcı', 'tag_id': isitici_tag_id, 'mod': 'toggle',
            'acik_deger': '1', 'kapali_deger': '0',
            'renk_acik': '#2ecc71', 'renk_kapali': '#e74c3c', 'renk_yazi': '#ffffff', 'custom_css': ''
        },
    ] + navbar_yansitilan
    sayfa_kaydet(cihaz_id, 'Ana Sayfa', ana_sayfa_mobil_elementleri, hedef='mobil',
                 tuval_w=420, tuval_h=860, arkaplan='#1e2d3d')


def _mobil_navbar_sablonu(sablon_kaynagi: str = None, kilitli: bool = False):
    """gemba projesindeki mobil alt-navbar + '+' speed-dial FAB düzeninden
    esinlenilmiş, oventechIOT'un kendi (koyu lacivert #17222e/#0f1720 +
    mavi accent #2e9ed9) paletiyle yeniden üretilmiş hazır mobil
    navigasyon elementleri. sablon_kaynagi/kilitli verilirse (bir sayfaya
    'Şablon Uygula' ile yansıtılmış gibi) o alanlar da eklenir; verilmezse
    şablonun KENDİ (kaynak, düzenlenebilir) hâli döner."""
    def _id():
        return 'el_' + secrets.token_hex(4)

    ekstra = {}
    if sablon_kaynagi:
        ekstra['sablon_kaynagi'] = sablon_kaynagi
    if kilitli:
        ekstra['kilitli'] = True

    def _sekil(**kw):
        d = {
            'id': _id(), 'type': 'sekil',
            'sekil_kenarlik': 'transparent', 'sekil_kenarlik_kalinlik': 0,
            'sekil_kose_solust': 0, 'sekil_kose_sagust': 0, 'sekil_kose_solalt': 0, 'sekil_kose_sagalt': 0,
            'sekil_gradient_aktif': False, 'sekil_gradient_renk1': '#2e9ed9', 'sekil_gradient_renk2': '#1f7bb0',
            'sekil_gradient_yon': 'to bottom', 'custom_css': '',
        }
        d.update(kw)
        d.update(ekstra)
        return d

    def _label(**kw):
        d = {
            'id': _id(), 'type': 'label', 'tag_id': None,
            'renk_arkaplan': 'transparent', 'custom_css': '',
        }
        d.update(kw)
        d.update(ekstra)
        return d

    return [
        # Alt navbar zemini — üst köşeleri yuvarlak, gradient koyu lacivert.
        _sekil(
            x=0, y=796, w=420, h=64,
            sekil_dolgu='#17222e', sekil_kenarlik='#2a3b4c', sekil_kenarlik_kalinlik=1,
            sekil_kose_solust=20, sekil_kose_sagust=20,
            sekil_gradient_aktif=True, sekil_gradient_renk1='#1e2d3d', sekil_gradient_renk2='#0f1720',
            sekil_gradient_yon='to bottom',
            custom_css='box-shadow: 0 -4px 20px rgba(0,0,0,0.35);',
        ),
        # "Ana Sayfa" sekmesinin aktif olduğunu gösteren yarı saydam pill —
        # gemba'daki `.bottom-nav-item.active .icon` vurgusunun karşılığı.
        # Etiketten ÖNCE eklenir ki katman sırasında ARKADA kalsın.
        _sekil(
            x=16, y=800, w=80, h=30,
            sekil_dolgu='#2e9ed933', sekil_kenarlik='transparent', sekil_kenarlik_kalinlik=0,
            sekil_kose_solust=15, sekil_kose_sagust=15, sekil_kose_solalt=15, sekil_kose_sagalt=15,
        ),
        # 4 navbar sekmesi (ikon üstte, metin altta) — ilki (Ana Sayfa) aktif renkte.
        _label(x=8, y=806, w=96, h=46, label='🏠\nAna Sayfa', renk_yazi='#ffffff', font_boyutu=11,
               custom_css='white-space:pre-line;line-height:1.5;font-weight:700;'),
        _label(x=112, y=806, w=96, h=46, label='🔥\nFırınlar', renk_yazi='#cfd8e3', font_boyutu=11,
               custom_css='white-space:pre-line;line-height:1.5;font-weight:600;'),
        _label(x=216, y=806, w=96, h=46, label='📊\nVeriler', renk_yazi='#cfd8e3', font_boyutu=11,
               custom_css='white-space:pre-line;line-height:1.5;font-weight:600;'),
        _label(x=320, y=806, w=92, h=46, label='⚙️\nAyarlar', renk_yazi='#cfd8e3', font_boyutu=11,
               custom_css='white-space:pre-line;line-height:1.5;font-weight:600;'),
        # "+" hızlı-erişim (speed dial) FAB — navbar'ın üzerinde yüzen daire.
        _sekil(
            x=348, y=726, w=56, h=56,
            sekil_dolgu='#2e9ed9', sekil_kenarlik='#1f7bb0', sekil_kenarlik_kalinlik=0,
            sekil_kose_solust=28, sekil_kose_sagust=28, sekil_kose_solalt=28, sekil_kose_sagalt=28,
            sekil_gradient_aktif=True, sekil_gradient_renk1='#2e9ed9', sekil_gradient_renk2='#1f7bb0',
            sekil_gradient_yon='to bottom',
            custom_css='box-shadow: 0 6px 16px rgba(0,0,0,0.4);',
        ),
        _label(x=348, y=726, w=56, h=56, label='+', renk_yazi='#ffffff', font_boyutu=30,
               custom_css='font-weight:700;'),
    ]


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
