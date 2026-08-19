from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from pages.login import login_required, tasarimci_required
import database

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def dashboard_page():
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


@dashboard_bp.route('/cihaz/<int:cihaz_id>')
@login_required
def cihaz_detay(cihaz_id):
    cihazlar = {c['id']: c for c in database.proje_cihazlari(session['proje_id'])}
    cihaz = cihazlar.get(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı ya da bu projeye ait değil.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    tagler = database.cihaz_tagleri(cihaz_id)
    sayfalar = database.cihaz_sayfalari(cihaz_id)
    return render_template('cihaz_detay.html', cihaz=cihaz, tagler=tagler, sayfalar=sayfalar)


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
