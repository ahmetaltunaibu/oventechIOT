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
import secrets
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

    # Not: sayfa oluştururken "şablon" seçme özelliği kaldırıldı — kullanıcı
    # isteği: aynı işlevi tasarım ekranında sağ tık > "📥 Şablon Uygula" zaten
    # sağlıyor (istediğin zaman ekleyip/geri alabiliyorsun), burada ayrıca
    # olmasına gerek yok.
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
        arkaplan_resim=sayfa.get('arkaplan_resim') or '',
        arkaplan_sigdirma=sayfa.get('arkaplan_sigdirma') or 'cover',
        arkaplan_gradient_aktif=bool(sayfa.get('arkaplan_gradient_aktif')),
        arkaplan_gradient_renk1=sayfa.get('arkaplan_gradient_renk1') or '#1e2d3d',
        arkaplan_gradient_renk2=sayfa.get('arkaplan_gradient_renk2') or '#0f1720',
        arkaplan_gradient_yon=sayfa.get('arkaplan_gradient_yon') or 'to bottom',
        sayfa_turu=sayfa.get('sayfa_turu') or 'normal',
        giris_animasyonu=sayfa.get('giris_animasyonu') or 'none',
        elementler_json=json.dumps(sayfa['elementler'], ensure_ascii=False),
        tagler_json=json.dumps(tagler, ensure_ascii=False),
    )


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/yeni-ada-kopyala', methods=['POST'])
@tasarimci_required
def sayfa_yeni_ada_kopyala(cihaz_id, sayfa_ad):
    """Kullanıcı isteği: bir sayfayı TAMAMEN kopyalayıp yeni (bağımsız)
    bir isimle kullanabilmeli — 'Şablon Uygula'dan farklı olarak burada
    canlı bağlantı yok, tek seferlik tam kopya. Sayfanın var olan HER
    düzeni (masaüstü + mobil, hangileri varsa) yeni ada kopyalanır."""
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    yeni_ad = (request.get_json(silent=True) or {}).get('yeni_ad', '').strip()
    if not yeni_ad:
        return jsonify({'error': 'Yeni sayfa adı zorunlu'}), 400
    if yeni_ad == sayfa_ad:
        return jsonify({'error': 'Yeni ad, mevcut sayfayla aynı olamaz'}), 400
    kopyalanan = 0
    for hedef in HEDEFLER:
        kaynak = database.sayfa_getir(cihaz_id, sayfa_ad, hedef)
        if not kaynak:
            continue
        if database.sayfa_getir(cihaz_id, yeni_ad, hedef):
            return jsonify({'error': f'"{yeni_ad}" adında bir sayfa ({hedef}) zaten var'}), 400
        # Element id'leri çakışmasın diye her elemente yeni id veriliyor.
        yeni_elementler = []
        for el in kaynak['elementler']:
            e = dict(el)
            e['id'] = 'el_' + secrets.token_hex(4)
            yeni_elementler.append(e)
        database.sayfa_kaydet(
            cihaz_id, yeni_ad, yeni_elementler, hedef=hedef,
            tuval_w=kaynak['tuval_w'], tuval_h=kaynak['tuval_h'], arkaplan=kaynak['arkaplan'],
            arkaplan_resim=kaynak.get('arkaplan_resim'), arkaplan_sigdirma=kaynak.get('arkaplan_sigdirma'),
            arkaplan_gradient_aktif=kaynak.get('arkaplan_gradient_aktif'),
            arkaplan_gradient_renk1=kaynak.get('arkaplan_gradient_renk1'),
            arkaplan_gradient_renk2=kaynak.get('arkaplan_gradient_renk2'),
            arkaplan_gradient_yon=kaynak.get('arkaplan_gradient_yon'),
            sayfa_turu=kaynak.get('sayfa_turu'), giris_animasyonu=kaynak.get('giris_animasyonu'),
        )
        kopyalanan += 1
    if not kopyalanan:
        return jsonify({'error': 'Kopyalanacak sayfa bulunamadı'}), 404
    return jsonify({'success': True, 'yeni_ad': yeni_ad})


@sayfa_bp.route('/cihaz/<int:cihaz_id>/sayfa/<sayfa_ad>/adini-degistir', methods=['POST'])
@tasarimci_required
def sayfa_adini_degistir(cihaz_id, sayfa_ad):
    """Kullanıcı isteği: bir sayfanın adını değiştirebilmeli. Masaüstü +
    mobil düzeni birlikte yeniden adlandırılır, diğer sayfalardaki
    'Sayfaya Git' butonlarının hedefleri de otomatik güncellenir."""
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    yeni_ad = (request.get_json(silent=True) or {}).get('yeni_ad', '').strip()
    ok, hata = database.sayfa_adini_degistir(cihaz_id, sayfa_ad, yeni_ad)
    if not ok:
        return jsonify({'error': hata}), 400
    return jsonify({'success': True, 'yeni_ad': yeni_ad})


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
        database.sayfa_kaydet(cihaz_id, sayfa_ad, diger['elementler'], hedef=hedef, arkaplan=diger['arkaplan'],
                               arkaplan_resim=diger.get('arkaplan_resim'), arkaplan_sigdirma=diger.get('arkaplan_sigdirma'),
                               arkaplan_gradient_aktif=diger.get('arkaplan_gradient_aktif'),
                               arkaplan_gradient_renk1=diger.get('arkaplan_gradient_renk1'),
                               arkaplan_gradient_renk2=diger.get('arkaplan_gradient_renk2'),
                               arkaplan_gradient_yon=diger.get('arkaplan_gradient_yon'),
                               sayfa_turu=diger.get('sayfa_turu'), giris_animasyonu=diger.get('giris_animasyonu'))
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
        tuval_w=veri.get('tuval_w'), tuval_h=veri.get('tuval_h'), arkaplan=veri.get('arkaplan'),
        arkaplan_resim=veri.get('arkaplan_resim'), arkaplan_sigdirma=veri.get('arkaplan_sigdirma'),
        arkaplan_gradient_aktif=veri.get('arkaplan_gradient_aktif'),
        arkaplan_gradient_renk1=veri.get('arkaplan_gradient_renk1'),
        arkaplan_gradient_renk2=veri.get('arkaplan_gradient_renk2'),
        arkaplan_gradient_yon=veri.get('arkaplan_gradient_yon'),
        sayfa_turu=veri.get('sayfa_turu'), giris_animasyonu=veri.get('giris_animasyonu')
    )
    # Sayfadaki her "alarm" elementi için sunucu-taraflı bir alarm kuralı
    # kaydediliyor — tarayıcı kapalı olsa bile ESP32 xchange'inde bu kural
    # değerlendirilebilsin diye (bkz. database.alarm_degerlendir).
    for el in veri['elementler']:
        if el.get('type') == 'alarm' and el.get('tag_id'):
            database.alarm_kural_kaydet(
                el['id'], cihaz_id, el['tag_id'], el.get('tip') or 'bool',
                bool_tetik_deger=el.get('bool_tetik_deger') or '1',
                karsilastirma=el.get('karsilastirma') or '>',
                esik_deger=el.get('esik_deger') or 0,
                mesaj=el.get('mesaj') or '',
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
            'arkaplan': s['arkaplan'], 'arkaplan_resim': s.get('arkaplan_resim'),
            'arkaplan_sigdirma': s.get('arkaplan_sigdirma') or 'cover',
            'arkaplan_gradient_aktif': bool(s.get('arkaplan_gradient_aktif')),
            'arkaplan_gradient_renk1': s.get('arkaplan_gradient_renk1') or '#1e2d3d',
            'arkaplan_gradient_renk2': s.get('arkaplan_gradient_renk2') or '#0f1720',
            'arkaplan_gradient_yon': s.get('arkaplan_gradient_yon') or 'to bottom',
            'sayfa_turu': s.get('sayfa_turu') or 'normal',
            'giris_animasyonu': s.get('giris_animasyonu') or 'none',
        }, ensure_ascii=False)

    # Kullanıcı isteği: tam ekran (üst çubuk + sayfa-nav gizli) SADECE
    # operatör girişinde — admin/tasarımcı eski yapıyı (üst çubuk + sayfa
    # geçiş çubuğu) test/önizleme amacıyla görmeye devam eder.
    tam_ekran = session.get('rol') == 'operator'
    return render_template(
        'sayfa_calistir.html',
        cihaz=cihaz, sayfa_ad=sayfa_ad,
        masaustu_json=paket(masaustu), mobil_json=paket(mobil),
        sayfalar=database.cihaz_sayfalari(cihaz_id),
        sayfa_bilgileri_json=json.dumps(database.cihaz_sayfa_bilgileri(cihaz_id), ensure_ascii=False),
        tam_ekran=tam_ekran,
    )


@sayfa_bp.route('/api/sablon-elementleri/<int:kaynak_cihaz_id>/<sayfa_ad>')
@tasarimci_required
def api_sablon_elementleri(kaynak_cihaz_id, sayfa_ad):
    """Kullanıcı isteği: örnek bir sayfa oluşturduktan sonra o sayfanın
    elementlerini istediği ZAMAN, VAR OLAN başka bir sayfaya da EKLEYEBİLMELİ
    (sadece sayfa oluştururken değil) — birden fazla şablonu aynı sayfada
    birleştirebilmesi için bu EKLEME (üzerine yazmadan) olarak yapılır.

    Kullanıcı isteği (bug fix): şablon MOBİL düzen tasarlanırken uygulanıyorsa
    kaynağın MOBİL elementleri, MASAÜSTÜ tasarlanırken kaynağın MASAÜSTÜ
    elementleri gelmeli — önceden her zaman masaüstü tercih ediliyordu.
    ?hedef=masaustu|mobil ile hangi düzenin isteneceği belirtilir; o düzen
    kaynakta yoksa (sadece o zaman) diğerine düşülür."""
    if not _cihaz_dogrula(kaynak_cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    istenen_hedef = request.args.get('hedef', 'masaustu')
    if istenen_hedef not in HEDEFLER:
        istenen_hedef = 'masaustu'
    diger_hedef = 'mobil' if istenen_hedef == 'masaustu' else 'masaustu'

    sayfa = database.sayfa_getir(kaynak_cihaz_id, sayfa_ad, istenen_hedef)
    hedef_kullanilan = istenen_hedef
    if not sayfa:
        sayfa = database.sayfa_getir(kaynak_cihaz_id, sayfa_ad, diger_hedef)
        hedef_kullanilan = diger_hedef
    if not sayfa:
        return jsonify({'error': 'Sayfa bulunamadı'}), 404
    return jsonify({
        'elementler': sayfa['elementler'],
        'tuval_w': sayfa['tuval_w'],
        'tuval_h': sayfa['tuval_h'],
        'hedef_kullanilan': hedef_kullanilan,
        'istenen_hedef': istenen_hedef,
    })


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


@sayfa_bp.route('/api/cihaz/<int:cihaz_id>/son-gorulme')
@login_required
def api_son_gorulme(cihaz_id):
    """Kullanıcı isteği: 'Son Veri' elementi için — cihazdan ESP32'nin en
    son ne zaman haber verdiğini (saniye cinsinden) döner."""
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    return jsonify({'saniye_once': database.cihaz_son_gorulme_saniye_once(cihaz_id)})


@sayfa_bp.route('/api/cihaz/<int:cihaz_id>/alarm-gecmisi')
@login_required
def api_alarm_gecmisi(cihaz_id):
    """"Alarm Geçmişi" elementi için — kullanıcı isteği: tekil alarm
    kuralları zaten alarm_kayitlari'na yazıyor, ama bunu görmek eskiden
    sadece cihaz_detay (tasarımcı/admin) sayfasındaydı. Artık operatör
    sayfalarına da eklenebilen bir element bunu okuyabiliyor —
    {kayitlar: [{tag_ad, mesaj, deger, olusma_zamani, giderilme_zamani}, ...]}"""
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    limit = min(max(request.args.get('limit', 50, type=int), 1), 200)
    sadece_aktif = request.args.get('sadece_aktif') == '1'
    kayitlar = database.alarm_kayitlari_listele(cihaz_id, limit)
    if sadece_aktif:
        kayitlar = [k for k in kayitlar if not k.get('giderilme_zamani')]
    return jsonify({'kayitlar': kayitlar})


@sayfa_bp.route('/api/cihaz/<int:cihaz_id>/gecmis')
@login_required
def api_gecmis(cihaz_id):
    """Grafik elementi için — birden fazla tag'in geçmiş değerlerini tek
    seferde döner: {tag_id: [{deger, zaman}, ...]}.
    ?tagler=1,2,3&limit=100 (canlı/varsayılan mod — son N ham satır) ya da
    ?tagler=1,2,3&aralik=1sa (hazır aralık butonları — 15dk..3ay, bkz.
    database.ARALIK_TANIMLARI; kısa aralıklar ham veriden, uzun aralıklar
    (1hf/1ay/3ay) saatlik özet tablosundan gelir)."""
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    tagler_param = request.args.get('tagler', '')
    try:
        tag_idler = [int(t) for t in tagler_param.split(',') if t.strip()]
    except ValueError:
        return jsonify({'error': 'Geçersiz tagler parametresi'}), 400
    if not tag_idler:
        return jsonify({})
    # Bu cihaza ait olmayan tag id'leri sızdırmayalım.
    cihaz_tag_idleri = {t['id'] for t in database.cihaz_tagleri(cihaz_id)}
    tag_idler = [t for t in tag_idler if t in cihaz_tag_idleri]
    aralik = request.args.get('aralik')
    if aralik and aralik in database.ARALIK_TANIMLARI:
        return jsonify(database.tagler_deger_gecmisi_araliktan(tag_idler, aralik))
    limit = min(max(request.args.get('limit', 100, type=int), 2), 500)
    return jsonify(database.tagler_deger_gecmisi(tag_idler, limit))


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
