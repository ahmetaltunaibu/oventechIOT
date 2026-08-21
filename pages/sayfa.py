"""Sayfa/element editörü ve runtime görünümü (Step 2 — Button elementi).

Bir sayfa "ad" (mantıksal isim, örn. "Ana Sayfa") altında 1-2 ayrı DÜZEN
(hedef) tutabilir: 'masaustu' ve 'mobil'. Her düzenin kendi tuval boyutu ve
element listesi vardır. Runtime'da tarayıcı genişliğine göre uygun düzen
seçilir; mobil düzen tanımlanmamışsa masaüstü düzeni ölçeklenerek kullanılır.

`sayfalar.elementler` JSON liste olarak saklanır:
  [{id, type:'button', x, y, w, h, label, tag_id, mod,
    acik_deger, kapali_deger, renk_acik, renk_kapali, renk_yazi}, ...]

mod: 'set_on' | 'set_off' | 'toggle' | 'momentary'
"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, Response
from pages.login import login_required, tasarimci_required
import database

sayfa_bp = Blueprint('sayfa', __name__)

HEDEFLER = ('masaustu', 'mobil')
_IZINLI_MIME = {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml'}
_MAKS_BOYUT = 4 * 1024 * 1024  # 4 MB — DB'ye BLOB olarak yaziliyor, cok buyumesin


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
    if database.sayfa_getir(cihaz_id, ad, 'masaustu'):
        flash('Bu isimde bir sayfa zaten var.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))

    kaynak = request.form.get('sablon', '').strip()  # "cihaz_id:sayfa_ad" ya da bos
    if kaynak and ':' in kaynak:
        kaynak_cihaz_id_str, kaynak_sayfa_ad = kaynak.split(':', 1)
        try:
            kaynak_cihaz_id = int(kaynak_cihaz_id_str)
        except ValueError:
            kaynak_cihaz_id = None
        # Sablon, oturumdaki projeye ait bir cihazdan olmali (baska projenin
        # verisi sizmasin diye).
        if kaynak_cihaz_id and _cihaz_dogrula(kaynak_cihaz_id):
            kopyalandi = False
            for hedef in HEDEFLER:
                kaynak_sayfa = database.sayfa_getir(kaynak_cihaz_id, kaynak_sayfa_ad, hedef)
                if kaynak_sayfa:
                    database.sayfa_kaydet(
                        cihaz_id, ad, kaynak_sayfa['elementler'], hedef=hedef,
                        tuval_w=kaynak_sayfa['tuval_w'], tuval_h=kaynak_sayfa['tuval_h'],
                        arkaplan=kaynak_sayfa['arkaplan'],
                    )
                    kopyalandi = True
            if kopyalandi:
                flash(f"Sayfa '{kaynak_sayfa_ad}' şablonundan oluşturuldu.", 'success')
                return redirect(url_for('sayfa.sayfa_tasarla', cihaz_id=cihaz_id, sayfa_ad=ad, hedef='masaustu'))

    database.sayfa_kaydet(cihaz_id, ad, [], hedef='masaustu')
    return redirect(url_for('sayfa.sayfa_tasarla', cihaz_id=cihaz_id, sayfa_ad=ad, hedef='masaustu'))


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/sil', methods=['POST'])
@tasarimci_required
def sayfa_sil(cihaz_id, sayfa_ad):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    database.sayfa_sil(cihaz_id, sayfa_ad)
    flash('Sayfa silindi.', 'success')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/<hedef>/sil', methods=['POST'])
@tasarimci_required
def sayfa_varyant_sil(cihaz_id, sayfa_ad, hedef):
    if not _cihaz_dogrula(cihaz_id) or hedef not in HEDEFLER:
        flash('Geçersiz istek.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    database.sayfa_varyant_sil(cihaz_id, sayfa_ad, hedef)
    flash(f"{'Mobil' if hedef == 'mobil' else 'Masaüstü'} düzeni silindi.", 'success')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/tasarla')
@tasarimci_required
def sayfa_tasarla(cihaz_id, sayfa_ad):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))

    hedef = request.args.get('hedef', 'masaustu')
    if hedef not in HEDEFLER:
        hedef = 'masaustu'

    sayfa = database.sayfa_getir(cihaz_id, sayfa_ad, hedef)
    if not sayfa:
        # Bu düzen (örn. mobil) henüz yok — diğer düzenden kopyalamayı öner,
        # yoksa boş başlat.
        diger_hedef = 'mobil' if hedef == 'masaustu' else 'masaustu'
        diger = database.sayfa_getir(cihaz_id, sayfa_ad, diger_hedef)
        if diger is None:
            flash('Sayfa bulunamadı.', 'danger')
            return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))
        sayfa = {'elementler': [], 'tuval_w': None, 'tuval_h': None, 'arkaplan': None}

    varsayilan_w = 1280 if hedef == 'masaustu' else 420
    varsayilan_h = 800 if hedef == 'masaustu' else 860

    diger_hedef = 'mobil' if hedef == 'masaustu' else 'masaustu'
    diger_var_mi = database.sayfa_getir(cihaz_id, sayfa_ad, diger_hedef) is not None

    tagler = database.cihaz_tagleri(cihaz_id)
    sayfalar = database.cihaz_sayfalari(cihaz_id)
    proje_sayfalari = database.proje_tum_sayfalari(session['proje_id'])
    return render_template(
        'sayfa_tasarla.html',
        cihaz=cihaz, sayfa_ad=sayfa_ad, hedef=hedef, diger_hedef=diger_hedef, diger_var_mi=diger_var_mi,
        sayfalar=sayfalar, proje_sayfalari=proje_sayfalari,
        tuval_w=sayfa.get('tuval_w') or varsayilan_w,
        tuval_h=sayfa.get('tuval_h') or varsayilan_h,
        arkaplan=sayfa.get('arkaplan') or '#1e2d3d',
        elementler_json=json.dumps(sayfa['elementler'], ensure_ascii=False),
        tagler_json=json.dumps(tagler, ensure_ascii=False),
    )


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/<hedef>/diger-duzenden-kopyala', methods=['POST'])
@tasarimci_required
def sayfa_kopyala(cihaz_id, sayfa_ad, hedef):
    if not _cihaz_dogrula(cihaz_id) or hedef not in HEDEFLER:
        flash('Geçersiz istek.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    diger_hedef = 'mobil' if hedef == 'masaustu' else 'masaustu'
    diger = database.sayfa_getir(cihaz_id, sayfa_ad, diger_hedef)
    if not diger:
        flash('Kopyalanacak diğer düzen bulunamadı.', 'danger')
    else:
        database.sayfa_kaydet(cihaz_id, sayfa_ad, diger['elementler'], hedef=hedef, arkaplan=diger['arkaplan'])
        flash('Diğer düzenden kopyalandı — konumları yeni tuvala göre ayarlamayı unutma.', 'success')
    return redirect(url_for('sayfa.sayfa_tasarla', cihaz_id=cihaz_id, sayfa_ad=sayfa_ad, hedef=hedef))


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/<hedef>/kaydet', methods=['POST'])
@tasarimci_required
def sayfa_kaydet(cihaz_id, sayfa_ad, hedef):
    if not _cihaz_dogrula(cihaz_id) or hedef not in HEDEFLER:
        return jsonify({'error': 'Geçersiz istek'}), 400
    veri = request.get_json(silent=True)
    if veri is None or not isinstance(veri, dict) or not isinstance(veri.get('elementler'), list):
        return jsonify({'error': 'Geçersiz veri'}), 400
    database.sayfa_kaydet(
        cihaz_id, sayfa_ad, veri['elementler'], hedef=hedef,
        tuval_w=veri.get('tuval_w'), tuval_h=veri.get('tuval_h'), arkaplan=veri.get('arkaplan')
    )
    return jsonify({'success': True})


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>')
@login_required
def sayfa_calistir(cihaz_id, sayfa_ad):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))

    masaustu = database.sayfa_getir(cihaz_id, sayfa_ad, 'masaustu')
    mobil = database.sayfa_getir(cihaz_id, sayfa_ad, 'mobil')
    if not masaustu and not mobil:
        flash('Sayfa bulunamadı.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))

    def paket(s):
        if not s:
            return 'null'
        return json.dumps({
            'elementler': s['elementler'], 'tuval_w': s['tuval_w'], 'tuval_h': s['tuval_h'],
            'arkaplan': s['arkaplan']
        }, ensure_ascii=False)

    return render_template(
        'sayfa_calistir.html',
        cihaz=cihaz, sayfa_ad=sayfa_ad,
        masaustu_json=paket(masaustu), mobil_json=paket(mobil),
        sayfalar=database.cihaz_sayfalari(cihaz_id),
    )


@sayfa_bp.route('/api/sablon-elementleri/<int:kaynak_cihaz_id>/<sayfa_ad>')
@tasarimci_required
def api_sablon_elementleri(kaynak_cihaz_id, sayfa_ad):
    """Kullanıcı isteği: örnek bir sayfa oluşturduktan sonra o sayfanın
    elementlerini istediği ZAMAN, VAR OLAN başka bir sayfaya da EKLEYEBİLMELİ
    (sadece sayfa oluştururken değil) — birden fazla şablonu aynı sayfada
    birleştirebilmesi için bu EKLEME (üzerine yazmadan) olarak yapılır."""
    if not _cihaz_dogrula(kaynak_cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    sayfa = database.sayfa_getir(kaynak_cihaz_id, sayfa_ad, 'masaustu') \
        or database.sayfa_getir(kaynak_cihaz_id, sayfa_ad, 'mobil')
    if not sayfa:
        return jsonify({'error': 'Sayfa bulunamadı'}), 404
    return jsonify({'elementler': sayfa['elementler']})


@sayfa_bp.route('/cihaz/<int:cihaz_id>/medya-yukle', methods=['POST'])
@tasarimci_required
def medya_yukle(cihaz_id):
    """Resim elementi için gerçek dosya yükleme — kullanıcı isteği: sadece
    dış URL girmek yeterli değildi, kendi resmini yükleyebilmeli. Dosya
    DB'ye BLOB olarak yazılır (Render'ın diski deploy'da sıfırlanıyor,
    ama .db zaten yedekleniyor — böylece yüklenen resimler de o yedekle
    birlikte korunur)."""
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    dosya = request.files.get('dosya')
    if not dosya or dosya.filename == '':
        return jsonify({'error': 'Dosya gönderilmedi'}), 400
    mime_tipi = dosya.mimetype or 'application/octet-stream'
    if mime_tipi not in _IZINLI_MIME:
        return jsonify({'error': f'Desteklenmeyen dosya türü: {mime_tipi}. İzin verilenler: PNG, JPEG, GIF, WEBP, SVG.'}), 400
    veri = dosya.read()
    if len(veri) > _MAKS_BOYUT:
        return jsonify({'error': 'Dosya çok büyük (maksimum 4 MB).'}), 400
    medya_id = database.medya_yukle(cihaz_id, dosya.filename, mime_tipi, veri)
    return jsonify({'success': True, 'url': url_for('sayfa.medya_goster', medya_id=medya_id)})


@sayfa_bp.route('/medya/<int:medya_id>')
@login_required
def medya_goster(medya_id):
    kayit = database.medya_getir(medya_id)
    if not kayit:
        return jsonify({'error': 'Bulunamadı'}), 404
    if not _cihaz_dogrula(kayit['cihaz_id']):
        return jsonify({'error': 'Yetkisiz'}), 403
    return Response(kayit['veri'], mimetype=kayit['mime_tipi'])


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
