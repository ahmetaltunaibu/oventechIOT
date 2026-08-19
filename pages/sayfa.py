"""Sayfa/element editörü ve runtime görünümü (Step 2 — Button elementi).

Bir sayfa, `sayfalar.elementler` kolonunda JSON liste olarak saklanır:
  [{id, type:'button', x, y, w, h, label, tag_id, mod,
    acik_deger, kapali_deger, renk_acik, renk_kapali, renk_yazi}, ...]

mod: 'set_on' | 'set_off' | 'toggle' | 'momentary'
"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from pages.login import login_required, tasarimci_required
import database

sayfa_bp = Blueprint('sayfa', __name__)


def _cihaz_dogrula(cihaz_id):
    cihazlar = {c['id']: c for c in database.proje_cihazlari(session['proje_id'])}
    return cihazlar.get(cihaz_id)


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa-olustur', methods=['POST'])
@tasarimci_required
def sayfa_olustur(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    ad = request.form.get('ad', '').strip()
    if not ad:
        flash('Sayfa adı zorunlu.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))
    if database.sayfa_getir(cihaz_id, ad):
        flash('Bu isimde bir sayfa zaten var.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))
    database.sayfa_kaydet(cihaz_id, ad, [])
    return redirect(url_for('sayfa.sayfa_tasarla', cihaz_id=cihaz_id, sayfa_ad=ad))


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/sil', methods=['POST'])
@tasarimci_required
def sayfa_sil(cihaz_id, sayfa_ad):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    database.sayfa_sil(cihaz_id, sayfa_ad)
    flash('Sayfa silindi.', 'success')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/tasarla')
@tasarimci_required
def sayfa_tasarla(cihaz_id, sayfa_ad):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    sayfa = database.sayfa_getir(cihaz_id, sayfa_ad)
    if not sayfa:
        flash('Sayfa bulunamadı.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))
    tagler = database.cihaz_tagleri(cihaz_id)
    return render_template(
        'sayfa_tasarla.html',
        cihaz=cihaz, sayfa_ad=sayfa_ad,
        elementler_json=json.dumps(sayfa['elementler'], ensure_ascii=False),
        tagler_json=json.dumps(tagler, ensure_ascii=False),
    )


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/kaydet', methods=['POST'])
@tasarimci_required
def sayfa_kaydet(cihaz_id, sayfa_ad):
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    elementler = request.get_json(silent=True)
    if elementler is None or not isinstance(elementler, list):
        return jsonify({'error': 'Geçersiz veri'}), 400
    database.sayfa_kaydet(cihaz_id, sayfa_ad, elementler)
    return jsonify({'success': True})


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>')
@login_required
def sayfa_calistir(cihaz_id, sayfa_ad):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    sayfa = database.sayfa_getir(cihaz_id, sayfa_ad)
    if not sayfa:
        flash('Sayfa bulunamadı.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))
    return render_template(
        'sayfa_calistir.html',
        cihaz=cihaz, sayfa_ad=sayfa_ad,
        elementler_json=json.dumps(sayfa['elementler'], ensure_ascii=False),
    )


# ============================================================
# API — runtime değer okuma / yazma
# ============================================================

@sayfa_bp.route('/api/cihaz/<int:cihaz_id>/degerler')
@login_required
def api_degerler(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    return jsonify(database.cihaz_tag_degerleri(cihaz_id))


@sayfa_bp.route('/api/cihaz/<int:cihaz_id>/tag/<int:tag_id>/yaz', methods=['POST'])
@login_required
def api_tag_yaz(cihaz_id, tag_id):
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    tag = database.tag_getir(tag_id)
    if not tag or tag['cihaz_id'] != cihaz_id:
        return jsonify({'error': 'Tag bulunamadı'}), 404
    if tag['erisim'] not in ('write', 'readwrite'):
        return jsonify({'error': 'Bu tag yazmaya açık değil'}), 403
    veri = request.get_json(silent=True) or {}
    if 'deger' not in veri:
        return jsonify({'error': 'deger alanı gerekli'}), 400
    database.tag_yaz_iste(tag_id, veri['deger'])
    return jsonify({'success': True})
