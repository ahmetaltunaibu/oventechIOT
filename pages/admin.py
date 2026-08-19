"""Platform yöneticisi (admin) — proje bağımsız giriş, tüm projeleri
görür/yönetir, yeni proje açar. Kimlik bilgisi ortam değişkeninde
(PLATFORM_ADMIN_USER / PLATFORM_ADMIN_PASSWORD), veritabanında kaydı yok.
"""
import os
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


@admin_bp.route('/proje-ekle', methods=['POST'])
@admin_required
def proje_ekle():
    kod = request.form.get('kod', '').strip()
    ad = request.form.get('ad', '').strip()
    if not kod or not ad:
        flash('Proje kodu ve adı zorunlu.', 'danger')
        return redirect(url_for('admin.admin_page'))
    ok, sonuc = database.proje_ekle(kod, ad)
    if ok:
        flash(f'Proje oluşturuldu: {ad}', 'success')
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
    return render_template('admin_proje.html', proje=proje, kullanicilar=kullanicilar, cihazlar=cihazlar)


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
