from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from pages.login import login_required, tasarimci_required
import database

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def dashboard_page():
    if not session.get('proje_id'):
        if session.get('platform_admin'):
            return redirect(url_for('admin.admin_page'))
        flash('Herhangi bir projeye bağlı değilsiniz.', 'danger')
        return redirect(url_for('login.login_page'))
    cihazlar = database.proje_cihazlari(session['proje_id'])
    return render_template('dashboard.html', cihazlar=cihazlar)


@dashboard_bp.route('/cihaz-ekle', methods=['POST'])
@tasarimci_required
def cihaz_ekle():
    ad = request.form.get('ad', '').strip()
    if not ad:
        flash('Cihaz adı zorunlu.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    ok, sonuc = database.cihaz_ekle(session['proje_id'], ad)
    if ok:
        flash(f"Cihaz eklendi. Cihaz Kimliği (ESP32 firmware'ine girilecek): {sonuc['cihaz_kimlik']}", 'success')
    else:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('dashboard.dashboard_page'))


def _cihaz_dogrula(cihaz_id):
    """Cihazın oturumdaki projeye ait olduğunu doğrular, yoksa None döner."""
    cihazlar = {c['id']: c for c in database.proje_cihazlari(session['proje_id'])}
    return cihazlar.get(cihaz_id)


@dashboard_bp.route('/cihaz/<int:cihaz_id>')
@login_required
def cihaz_detay(cihaz_id):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı ya da bu projeye ait değil.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    tagler = database.cihaz_tagleri(cihaz_id)
    sayfalar = database.cihaz_sayfalari(cihaz_id)
    return render_template('cihaz_detay.html', cihaz=cihaz, tagler=tagler, sayfalar=sayfalar)


@dashboard_bp.route('/cihaz/<int:cihaz_id>/yeniden-adlandir', methods=['POST'])
@tasarimci_required
def cihaz_yeniden_adlandir(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    yeni_ad = request.form.get('ad', '').strip()
    if not yeni_ad:
        flash('Cihaz adı zorunlu.', 'danger')
    else:
        database.cihaz_yeniden_adlandir(cihaz_id, yeni_ad)
        flash('Cihaz adı güncellendi.', 'success')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/sil', methods=['POST'])
@tasarimci_required
def cihaz_sil(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    database.cihaz_sil(cihaz_id)
    flash('Cihaz silindi.', 'success')
    return redirect(url_for('dashboard.dashboard_page'))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/tag/<int:tag_id>/sil', methods=['POST'])
@tasarimci_required
def tag_sil(cihaz_id, tag_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    database.tag_sil(tag_id)
    flash('Tag silindi.', 'success')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/tag/<int:tag_id>/duzenle', methods=['POST'])
@tasarimci_required
def tag_duzenle(cihaz_id, tag_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))

    ad           = request.form.get('ad', '').strip()
    modbus_adres = request.form.get('modbus_adres', '').strip()
    veri_tipi    = request.form.get('veri_tipi', 'bool')
    erisim       = request.form.get('erisim', 'read')

    if not ad or not modbus_adres:
        flash('Tag adı ve Modbus adresi zorunlu.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))

    ok, hata = database.tag_guncelle(tag_id, ad, modbus_adres, veri_tipi, erisim)
    if ok:
        flash('Tag güncellendi.', 'success')
    else:
        flash(f'Hata: {hata}', 'danger')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/tag-ekle', methods=['POST'])
@tasarimci_required
def tag_ekle(cihaz_id):
    cihazlar = {c['id']: c for c in database.proje_cihazlari(session['proje_id'])}
    if cihaz_id not in cihazlar:
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))

    ad           = request.form.get('ad', '').strip()
    modbus_adres = request.form.get('modbus_adres', '').strip()
    veri_tipi    = request.form.get('veri_tipi', 'bool')
    erisim       = request.form.get('erisim', 'read')

    if not ad or not modbus_adres:
        flash('Tag adı ve Modbus adresi zorunlu.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))

    ok, sonuc = database.tag_ekle(cihaz_id, ad, modbus_adres, veri_tipi, erisim)
    if not ok:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))
