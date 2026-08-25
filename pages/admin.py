"""Platform yöneticisi (admin) — proje bağımsız giriş, tüm projeleri
görür/yönetir, yeni proje açar. Kimlik bilgisi ortam değişkeninde
(PLATFORM_ADMIN_USER / PLATFORM_ADMIN_PASSWORD), veritabanında kaydı yok.
"""
import os
import re
import unicodedata
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
import database

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def _env_strip(ad):
    v = os.environ.get(ad)
    return v.strip() if v else v


PLATFORM_ADMIN_USER = _env_strip('PLATFORM_ADMIN_USER')
PLATFORM_ADMIN_PASSWORD = _env_strip('PLATFORM_ADMIN_PASSWORD')


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('platform_admin'):
            flash('Bu sayfa için platform yöneticisi girişi gerekiyor.', 'warning')
            return redirect(url_for('admin.admin_login'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        kullanici_adi = request.form.get('kullanici_adi', '').strip()
        sifre = request.form.get('sifre', '').strip()
        if not PLATFORM_ADMIN_USER or not PLATFORM_ADMIN_PASSWORD:
            flash('Sunucuda PLATFORM_ADMIN_USER / PLATFORM_ADMIN_PASSWORD ortam değişkenleri tanımlı değil.', 'danger')
            return redirect(url_for('admin.admin_login'))
        if (kullanici_adi == PLATFORM_ADMIN_USER and sifre == PLATFORM_ADMIN_PASSWORD):
            session.clear()
            session['platform_admin'] = True
            session['kullanici_adi'] = kullanici_adi
            return redirect(url_for('admin.admin_page'))
        flash('Kullanıcı adı ya da şifre hatalı.', 'danger')
        return redirect(url_for('admin.admin_login'))
    return render_template('admin_login.html')


@admin_bp.route('/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin.admin_login'))


@admin_bp.route('/')
@admin_required
def admin_page():
    projeler = database.proje_listesi()
    return render_template('admin.html', projeler=projeler)


def _slug_uret(metin: str) -> str:
    """'Oventech Test A.Ş.' -> 'oventech-test-as' gibi bir kod üretir.
    Proje kodu sadece iç kullanım içindir (DB'de benzersiz kimlik), kullanıcıya
    hiçbir yerde gösterilmez/girilmez."""
    normal = unicodedata.normalize('NFKD', metin).encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', normal).strip('-').lower()
    return slug or 'proje'


@admin_bp.route('/proje-ekle', methods=['POST'])
@admin_required
def proje_ekle():
    ad = request.form.get('ad', '').strip()
    if not ad:
        flash('Proje adı zorunlu.', 'danger')
        return redirect(url_for('admin.admin_page'))

    taban_kod = _slug_uret(ad)
    kod = taban_kod
    sayac = 2
    while database.proje_getir_kod(kod):
        kod = f'{taban_kod}-{sayac}'
        sayac += 1

    ok, sonuc = database.proje_ekle(kod, ad)
    if ok:
        flash(f'Proje oluşturuldu: {ad}', 'success')
    else:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('admin.admin_page'))


@admin_bp.route('/proje/<int:proje_id>/kopyala', methods=['POST'])
@admin_required
def proje_kopyala(proje_id):
    """Kullanıcı isteği: bir projeyi (tüm cihaz/tag/sayfa/medyasıyla) YENİ
    bir proje olarak kopyalar — yeni bir müşteri/kurulum için şablon.
    Kullanıcılar kopyalanmaz, cihazlara yeni cihaz_kimlik verilir (bkz.
    database.proje_kopyala docstring)."""
    kaynak = database.proje_getir(proje_id)
    if not kaynak:
        flash('Kaynak proje bulunamadı.', 'danger')
        return redirect(url_for('admin.admin_page'))
    yeni_ad = request.form.get('ad', '').strip() or f"{kaynak['ad']} (kopya)"

    taban_kod = _slug_uret(yeni_ad)
    kod = taban_kod
    sayac = 2
    while database.proje_getir_kod(kod):
        kod = f'{taban_kod}-{sayac}'
        sayac += 1

    ok, sonuc = database.proje_kopyala(proje_id, kod, yeni_ad)
    if ok:
        flash(f"Proje kopyalandı: {yeni_ad} — kullanıcı hesapları kopyalanmadı, cihazlara yeni (henüz hiçbir ESP32'ye bağlı olmayan) kimlikler verildi.", 'success')
    else:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('admin.admin_page'))


@admin_bp.route('/proje/<int:proje_id>/duzenle', methods=['POST'])
@admin_required
def proje_duzenle(proje_id):
    if not database.proje_getir(proje_id):
        flash('Proje bulunamadı.', 'danger')
        return redirect(url_for('admin.admin_page'))
    yeni_ad = request.form.get('ad', '').strip()
    if not yeni_ad:
        flash('Proje adı zorunlu.', 'danger')
    else:
        database.proje_yeniden_adlandir(proje_id, yeni_ad)
        flash('Proje adı güncellendi.', 'success')
    return redirect(url_for('admin.proje_detay', proje_id=proje_id))


@admin_bp.route('/proje/<int:proje_id>/sil', methods=['POST'])
@admin_required
def proje_sil(proje_id):
    if not database.proje_getir(proje_id):
        flash('Proje bulunamadı.', 'danger')
        return redirect(url_for('admin.admin_page'))
    database.proje_sil(proje_id)
    flash('Proje silindi.', 'success')
    return redirect(url_for('admin.admin_page'))


@admin_bp.route('/proje/<int:proje_id>/kullanici/<int:kullanici_id>/duzenle', methods=['POST'])
@admin_required
def kullanici_duzenle(proje_id, kullanici_id):
    if not database.proje_getir(proje_id):
        flash('Proje bulunamadı.', 'danger')
        return redirect(url_for('admin.admin_page'))

    ad_soyad = request.form.get('ad_soyad', '').strip()
    rol = request.form.get('rol', 'operator')
    yeni_sifre = request.form.get('yeni_sifre', '').strip()

    if not ad_soyad:
        flash('Ad soyad zorunlu.', 'danger')
        return redirect(url_for('admin.proje_detay', proje_id=proje_id))

    database.kullanici_guncelle(kullanici_id, ad_soyad, rol, yeni_sifre or None)
    # Kullanıcı isteği: 'operator' rolü artık sadece kendisine açıkça
    # verilen cihazları görsün — bu formdaki işaretli cihaz listesi.
    cihaz_idler = [int(x) for x in request.form.getlist('cihaz_idler') if x.isdigit()]
    database.kullanici_cihaz_erisimlerini_ayarla(kullanici_id, cihaz_idler)
    flash('Kullanıcı güncellendi.' + (' Şifre de değiştirildi.' if yeni_sifre else ''), 'success')
    return redirect(url_for('admin.proje_detay', proje_id=proje_id))


@admin_bp.route('/proje/<int:proje_id>/kullanici/<int:kullanici_id>/sil', methods=['POST'])
@admin_required
def kullanici_sil(proje_id, kullanici_id):
    if not database.proje_getir(proje_id):
        flash('Proje bulunamadı.', 'danger')
        return redirect(url_for('admin.admin_page'))
    database.kullanici_sil(kullanici_id)
    flash('Kullanıcı silindi.', 'success')
    return redirect(url_for('admin.proje_detay', proje_id=proje_id))


@admin_bp.route('/proje/<int:proje_id>')
@admin_required
def proje_detay(proje_id):
    proje = database.proje_getir(proje_id)
    if not proje:
        flash('Proje bulunamadı.', 'danger')
        return redirect(url_for('admin.admin_page'))
    kullanicilar = database.proje_kullanicilari(proje_id)
    cihazlar = database.proje_cihazlari(proje_id)
    kullanici_cihaz_erisimleri = {k['id']: database.kullanici_cihaz_erisim_idleri(k['id']) for k in kullanicilar}
    return render_template('admin_proje.html', proje=proje, kullanicilar=kullanicilar, cihazlar=cihazlar,
                            kullanici_cihaz_erisimleri=kullanici_cihaz_erisimleri)


@admin_bp.route('/proje/<int:proje_id>/kullanici-ekle', methods=['POST'])
@admin_required
def kullanici_ekle(proje_id):
    proje = database.proje_getir(proje_id)
    if not proje:
        flash('Proje bulunamadı.', 'danger')
        return redirect(url_for('admin.admin_page'))

    kullanici_adi = request.form.get('kullanici_adi', '').strip()
    sifre = request.form.get('sifre', '')
    ad_soyad = request.form.get('ad_soyad', '').strip()
    rol = request.form.get('rol', 'operator')

    if not kullanici_adi or not sifre or not ad_soyad:
        flash('Kullanıcı adı, şifre ve ad soyad zorunlu.', 'danger')
        return redirect(url_for('admin.proje_detay', proje_id=proje_id))

    ok, sonuc = database.kullanici_ekle(proje_id, kullanici_adi, sifre, ad_soyad, rol)
    if ok:
        cihaz_idler = [int(x) for x in request.form.getlist('cihaz_idler') if x.isdigit()]
        if cihaz_idler:
            database.kullanici_cihaz_erisimlerini_ayarla(sonuc, cihaz_idler)
        flash(f'Kullanıcı eklendi: {kullanici_adi}', 'success')
    else:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('admin.proje_detay', proje_id=proje_id))


@admin_bp.route('/proje/<int:proje_id>/gir')
@admin_required
def proje_gir(proje_id):
    """Admin, bu projenin cihaz/sayfa yönetim ekranına (normal dashboard) geçer."""
    proje = database.proje_getir(proje_id)
    if not proje:
        flash('Proje bulunamadı.', 'danger')
        return redirect(url_for('admin.admin_page'))
    session['proje_id'] = proje['id']
    session['proje_ad'] = proje['ad']
    return redirect(url_for('dashboard.dashboard_page'))


# ============================================================
# PLC PROFİLLERİ — kullanıcı isteği: PLC markası/serisi (Delta DVP, AS,
# 15MC, ileride Siemens vb.) için X/Y/M/D gibi adres öneklerinin hangi
# Modbus fonksiyonuna ve hangi formülle ham adrese gittiğini burada
# tanımlarız — tag eklerken artık ham adresi elle hesaplamak yerine
# "X21" gibi PLC'nin kendi gösterdiği adresi yazmak yeterli olur. Proje
# bağımsız (tüm projeler paylaşır), bu yüzden platform admin panelinde.
# ============================================================

@admin_bp.route('/plc-profilleri')
@admin_required
def plc_profilleri():
    profiller = database.plc_profilleri_listele()
    profiller_detayli = [database.plc_profil_getir(p['id']) for p in profiller]
    return render_template('plc_profilleri.html', profiller=profiller_detayli)


@admin_bp.route('/plc-profil-ekle', methods=['POST'])
@admin_required
def plc_profil_ekle():
    ad = request.form.get('ad', '').strip()
    aciklama = request.form.get('aciklama', '').strip()
    if not ad:
        flash('Profil adı zorunlu.', 'danger')
        return redirect(url_for('admin.plc_profilleri'))
    ok, sonuc = database.plc_profil_ekle(ad, aciklama)
    if ok:
        flash(f'Profil eklendi: {ad}', 'success')
    else:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('admin.plc_profilleri'))


@admin_bp.route('/plc-profil/<int:profil_id>/sil', methods=['POST'])
@admin_required
def plc_profil_sil(profil_id):
    database.plc_profil_sil(profil_id)
    flash('Profil silindi.', 'success')
    return redirect(url_for('admin.plc_profilleri'))


@admin_bp.route('/plc-profil/<int:profil_id>/bolge-kaydet', methods=['POST'])
@admin_required
def plc_profil_bolge_kaydet(profil_id):
    onek = request.form.get('onek', '').strip()
    modbus_fonksiyon = request.form.get('modbus_fonksiyon', '')
    sayi_sistemi = request.form.get('sayi_sistemi', 'onluk')
    varsayilan_veri_tipi = request.form.get('varsayilan_veri_tipi', 'bool')
    guven_notu = request.form.get('guven_notu', '').strip()
    if not onek:
        flash('Önek (örn. X, Y, M, D) zorunlu.', 'danger')
        return redirect(url_for('admin.plc_profilleri'))
    try:
        adres_tabani = int(request.form.get('adres_tabani', '0') or '0')
    except ValueError:
        flash('Taban ofset sayısal olmalı.', 'danger')
        return redirect(url_for('admin.plc_profilleri'))
    database.plc_profil_bolge_kaydet(profil_id, onek, modbus_fonksiyon, sayi_sistemi, adres_tabani, varsayilan_veri_tipi, guven_notu)
    flash(f'"{onek.upper()}" bölgesi kaydedildi.', 'success')
    return redirect(url_for('admin.plc_profilleri'))


@admin_bp.route('/plc-profil-bolge/<int:bolge_id>/sil', methods=['POST'])
@admin_required
def plc_profil_bolge_sil(bolge_id):
    database.plc_profil_bolge_sil(bolge_id)
    flash('Bölge silindi.', 'success')
    return redirect(url_for('admin.plc_profilleri'))
