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
            olusturma_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

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
                  tuval_w: int = None, tuval_h: int = None, arkaplan: str = None):
    varsayilan_w = 1280 if hedef == 'masaustu' else 420
    varsayilan_h = 800 if hedef == 'masaustu' else 860
    conn = get_db()
    try:
        conn.execute('''
            INSERT INTO sayfalar (cihaz_id, ad, hedef, tuval_w, tuval_h, arkaplan, elementler, guncelleme_zamani)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+3 hours'))
            ON CONFLICT(cihaz_id, ad, hedef) DO UPDATE SET
                elementler = excluded.elementler,
                tuval_w = excluded.tuval_w,
                tuval_h = excluded.tuval_h,
                arkaplan = excluded.arkaplan,
                guncelleme_zamani = datetime('now', '+3 hours')
        ''', (cihaz_id, ad.strip(), hedef, tuval_w or varsayilan_w, tuval_h or varsayilan_h,
              arkaplan or '#1e2d3d', json.dumps(elementler, ensure_ascii=False)))
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


def proje_tum_sayfalari(proje_id: int):
    """Projedeki TÜM cihazların TÜM sayfalarını (şablon seçimi için) döner:
    [{cihaz_id, cihaz_ad, sayfa_ad}, ...] — her mantıksal sayfa adı bir kez
    (hangi düzenleri olduğuna bakılmaksızın)."""
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT DISTINCT s.cihaz_id, c.ad AS cihaz_ad, s.ad AS sayfa_ad
            FROM sayfalar s
            JOIN cihazlar c ON c.id = s.cihaz_id
            WHERE c.proje_id = ?
            ORDER BY c.ad, s.ad
        ''', (proje_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
