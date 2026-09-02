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
    cihazlar = database.kullanici_erisebilir_cihazlar(session.get('kullanici_id'), session.get('rol'), session['proje_id'])
    # Kullanıcı isteği: tam ekran/kart görünümü SADECE operatör girişinde —
    # admin ve tasarımcı için eski yapı (üst çubuk + tablo) korunuyor,
    # onlar yönetim/tasarım yapıyor, "gerçek program" hissi operatöre özel.
    tam_ekran = session.get('rol') == 'operator'
    return render_template('dashboard.html', cihazlar=cihazlar, tam_ekran=tam_ekran)


@dashboard_bp.route('/cihaz-ekle', methods=['POST'])
@tasarimci_required
def cihaz_ekle():
    ad = request.form.get('ad', '').strip()
    if not ad:
        flash('Cihaz adı zorunlu.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    # Opsiyonel: daha önce kurulmuş bir ESP32'nin flash'ında hâlâ duran
    # kimliğini elle girip eşleştirmek için (yeniden kurulum gerekmez).
    # Boş bırakılırsa database.cihaz_ekle otomatik/rastgele üretir.
    mevcut_kimlik = request.form.get('cihaz_kimlik', '').strip()
    ok, sonuc = database.cihaz_ekle(session['proje_id'], ad, mevcut_kimlik)
    if ok:
        flash(f"Cihaz eklendi. Cihaz Kimliği (ESP32 firmware'ine girilecek): {sonuc['cihaz_kimlik']}", 'success')
    else:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('dashboard.dashboard_page'))


@dashboard_bp.route('/kullanicilar')
@tasarimci_required
def kullanicilar():
    proje_id = session['proje_id']
    kullanicilar = [k for k in database.proje_kullanicilari(proje_id) if k['rol'] == 'operator']
    cihazlar = database.proje_cihazlari(proje_id)
    kullanici_cihaz_erisimleri = {k['id']: database.kullanici_cihaz_erisim_idleri(k['id']) for k in kullanicilar}
    return render_template('kullanicilar.html', kullanicilar=kullanicilar, cihazlar=cihazlar,
                            kullanici_cihaz_erisimleri=kullanici_cihaz_erisimleri)


def _operator_dogrula(kullanici_id):
    """Hedef kullanıcının, oturumdaki projeye ait VE 'operator' rolünde
    olduğunu doğrular — tasarımcı bu uçlarla başka bir tasarımcıyı/admini
    ya da başka bir projenin kullanıcısını asla düzenleyemesin/silemesin."""
    hedef = database.kullanici_getir(kullanici_id)
    if not hedef or hedef['proje_id'] != session['proje_id'] or hedef['rol'] != 'operator':
        return None
    return hedef


@dashboard_bp.route('/kullanici-ekle', methods=['POST'])
@tasarimci_required
def kullanici_ekle():
    proje_id = session['proje_id']
    kullanici_adi = request.form.get('kullanici_adi', '').strip()
    sifre = request.form.get('sifre', '')
    ad_soyad = request.form.get('ad_soyad', '').strip()
    if not kullanici_adi or not sifre or not ad_soyad:
        flash('Kullanıcı adı, şifre ve ad soyad zorunlu.', 'danger')
        return redirect(url_for('dashboard.kullanicilar'))
    # Tasarımcı SADECE operatör ekleyebilir — rol formdan alınmıyor, sabit.
    ok, sonuc = database.kullanici_ekle(proje_id, kullanici_adi, sifre, ad_soyad, 'operator')
    if ok:
        cihaz_idler = [int(x) for x in request.form.getlist('cihaz_idler') if x.isdigit()]
        if cihaz_idler:
            database.kullanici_cihaz_erisimlerini_ayarla(sonuc, cihaz_idler)
        flash('Operatör eklendi.', 'success')
    else:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('dashboard.kullanicilar'))


@dashboard_bp.route('/kullanici/<int:kullanici_id>/duzenle', methods=['POST'])
@tasarimci_required
def kullanici_duzenle(kullanici_id):
    hedef = _operator_dogrula(kullanici_id)
    if not hedef:
        flash('Kullanıcı bulunamadı.', 'danger')
        return redirect(url_for('dashboard.kullanicilar'))
    ad_soyad = request.form.get('ad_soyad', '').strip()
    yeni_sifre = request.form.get('yeni_sifre', '').strip()
    if not ad_soyad:
        flash('Ad soyad zorunlu.', 'danger')
        return redirect(url_for('dashboard.kullanicilar'))
    # Rol yine sabit 'operator' — tasarımcı bir operatörü tasarımcı/admin yapamaz.
    database.kullanici_guncelle(kullanici_id, ad_soyad, 'operator', yeni_sifre or None)
    cihaz_idler = [int(x) for x in request.form.getlist('cihaz_idler') if x.isdigit()]
    database.kullanici_cihaz_erisimlerini_ayarla(kullanici_id, cihaz_idler)
    flash('Operatör güncellendi.' + (' Şifre de değiştirildi.' if yeni_sifre else ''), 'success')
    return redirect(url_for('dashboard.kullanicilar'))


@dashboard_bp.route('/kullanici/<int:kullanici_id>/sil', methods=['POST'])
@tasarimci_required
def kullanici_sil(kullanici_id):
    hedef = _operator_dogrula(kullanici_id)
    if not hedef:
        flash('Kullanıcı bulunamadı.', 'danger')
        return redirect(url_for('dashboard.kullanicilar'))
    database.kullanici_sil(kullanici_id)
    flash('Operatör silindi.', 'success')
    return redirect(url_for('dashboard.kullanicilar'))


def _cihaz_dogrula(cihaz_id):
    """Cihazın oturumdaki projeye ait olduğunu VE (operator ise) kullanıcının
    bu cihaza erişimi olduğunu doğrular, yoksa None döner."""
    cihazlar = {c['id']: c for c in database.kullanici_erisebilir_cihazlar(
        session.get('kullanici_id'), session.get('rol'), session['proje_id'])}
    return cihazlar.get(cihaz_id)


@dashboard_bp.route('/cihaz/<int:cihaz_id>')
@login_required
def cihaz_detay(cihaz_id):
    """"Cihaz Özellikleri" sayfası — cihaz adı/yeniden adlandırma/PLC profili/
    kopyalama/silme + bağlantı-modbus-WiFi durumu. Kullanıcı isteği: eskiden
    Alarm/Firmware/Tag'ler/Sayfalar da hep bu TEK dev sayfadaydı — artık her
    biri kendi sayfasında (bkz. cihaz_alarmlar/cihaz_firmware/cihaz_tagler/
    cihaz_sayfalar), aralarında _cihaz_altmenu.html sekme çubuğuyla geziliyor."""
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı ya da bu projeye ait değil.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    plc_profilleri = [database.plc_profil_getir(p['id']) for p in database.plc_profilleri_listele()]
    return render_template('cihaz_detay.html', cihaz=cihaz, plc_profilleri=plc_profilleri)


@dashboard_bp.route('/cihaz/<int:cihaz_id>/alarmlar')
@login_required
def cihaz_alarmlar(cihaz_id):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı ya da bu projeye ait değil.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    alarmlar = database.alarm_kayitlari_listele(cihaz_id, limit=30)
    return render_template('cihaz_alarmlar.html', cihaz=cihaz, alarmlar=alarmlar)


@dashboard_bp.route('/cihaz/<int:cihaz_id>/firmware')
@login_required
def cihaz_firmware(cihaz_id):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı ya da bu projeye ait değil.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    # Eskiden bu kart cihaz_detay.html içinde sadece platform_admin'e
    # gösteriliyordu (veri her rol için çekilse de) — aynı kısıtlama artık
    # route seviyesinde: diğer roller bu sayfaya hiç giremiyor.
    if not session.get('platform_admin'):
        flash('Bu sayfayı görüntüleme yetkin yok.', 'danger')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))
    firmwarelar = database.firmware_listesi(session['proje_id'])
    firmware_gecmisi = database.firmware_gecmisi_listele(cihaz_id, limit=15)
    return render_template('cihaz_firmware.html', cihaz=cihaz, firmwarelar=firmwarelar,
                            firmware_gecmisi=firmware_gecmisi)


@dashboard_bp.route('/cihaz/<int:cihaz_id>/tagler')
@login_required
def cihaz_tagler(cihaz_id):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı ya da bu projeye ait değil.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    tagler = database.cihaz_tagleri(cihaz_id)
    # Sadece id/ad değil, JS tarafında "Doğal Adres" alanının önek (X/Y/M/D)
    # combobox'ını doldurmak için her profilin bölgelerine (onek listesine)
    # de ihtiyaç var — bu yüzden düz listeleme yerine her profili tek tek
    # detaylı (bolgeler dahil) çekiyoruz.
    plc_profilleri = [database.plc_profil_getir(p['id']) for p in database.plc_profilleri_listele()]
    return render_template('cihaz_tagler.html', cihaz=cihaz, tagler=tagler, plc_profilleri=plc_profilleri)


@dashboard_bp.route('/cihaz/<int:cihaz_id>/sayfalar')
@login_required
def cihaz_sayfalar(cihaz_id):
    cihaz = _cihaz_dogrula(cihaz_id)
    if not cihaz:
        flash('Cihaz bulunamadı ya da bu projeye ait değil.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    sayfalar = database.cihaz_sayfalari(cihaz_id)
    return render_template('cihaz_sayfalar.html', cihaz=cihaz, sayfalar=sayfalar)


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


@dashboard_bp.route('/cihaz/<int:cihaz_id>/plc-profili', methods=['POST'])
@tasarimci_required
def cihaz_plc_profili(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    ham = request.form.get('plc_profil_id', '').strip()
    plc_profil_id = int(ham) if ham else None
    database.cihaz_plc_profili_ayarla(cihaz_id, plc_profil_id)
    flash('PLC profili güncellendi.' if plc_profil_id else 'PLC profili kaldırıldı.', 'success')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/baslangic-sayfa', methods=['POST'])
@tasarimci_required
def cihaz_baslangic_sayfa(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    sayfa_ad = request.form.get('sayfa_ad', '').strip()
    database.cihaz_baslangic_sayfa_guncelle(cihaz_id, sayfa_ad)
    flash('Başlangıç sayfası güncellendi.' if sayfa_ad else 'Başlangıç sayfası kaldırıldı — cihaza tıklayınca yine yönetim sayfası açılacak.', 'success')
    return redirect(url_for('dashboard.cihaz_sayfalar', cihaz_id=cihaz_id))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/wifi-sifirla-iste', methods=['POST'])
@tasarimci_required
def cihaz_wifi_sifirla_iste(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    database.cihaz_wifi_sifirlama_iste(cihaz_id)
    flash('WiFi sıfırlama isteği gönderildi — cihaz internete bağlıysa bir sonraki senkronunda (en fazla ~5sn) kendi WiFi ayarlarını unutup kurulum moduna girecek. Cihaz o an offline ise bu komuta ulaşamaz, tekrar denemen gerekir.', 'success')
    return redirect(url_for('dashboard.cihaz_detay', cihaz_id=cihaz_id))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/kopyala', methods=['POST'])
@tasarimci_required
def cihaz_kopyala(cihaz_id):
    """Kullanıcı isteği: proje kopyalama gibi ama TEK cihaz için — aynı
    projede tag/sayfa/medyasıyla birlikte yeni bir cihaz oluşturur."""
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    yeni_ad = request.form.get('ad', '').strip()
    ok, sonuc = database.cihaz_kopyala(cihaz_id, yeni_ad)
    if ok:
        flash(f'Cihaz kopyalandı — yeni cihaz henüz hiçbir ESP32\'ye bağlı değil, kimliğini yeni cihazın yönetim sayfasından görebilirsin.', 'success')
        return redirect(url_for('dashboard.cihaz_detay', cihaz_id=sonuc))
    flash(f'Hata: {sonuc}', 'danger')
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
    return redirect(url_for('dashboard.cihaz_tagler', cihaz_id=cihaz_id))


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
        return redirect(url_for('dashboard.cihaz_tagler', cihaz_id=cihaz_id))

    ok, hata = database.tag_guncelle(tag_id, ad, modbus_adres, veri_tipi, erisim)
    if ok:
        flash('Tag güncellendi.', 'success')
    else:
        flash(f'Hata: {hata}', 'danger')
    return redirect(url_for('dashboard.cihaz_tagler', cihaz_id=cihaz_id))


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
        return redirect(url_for('dashboard.cihaz_tagler', cihaz_id=cihaz_id))

    ok, sonuc = database.tag_ekle(cihaz_id, ad, modbus_adres, veri_tipi, erisim)
    if not ok:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('dashboard.cihaz_tagler', cihaz_id=cihaz_id))


# ============================================================
# TAG'LER — Excel-tarzı tablo (kullanıcı isteği: tag eklemek/düzenlemek
# eskiden her tag için ayrı bir kart+form+sayfa yenilemesi gerektiriyordu,
# çok yavaştı). Bu üç JSON uç, sayfayı hiç yenilemeden (fetch ile) hücre
# hücre kaydeden bir tablo için — yukarıdaki form-tabanlı uçlar (tag_ekle,
# tag_duzenle, tag_sil) JS kapalıyken devreye giren <noscript> yedek
# formu için hâlâ duruyor, silinmedi.
# ============================================================

def _dogal_adres_uygula(veri, mevcut_modbus_adres):
    """Kullanıcı isteği: PLC markası/serisi için bir "PLC Profili" seçilip
    "X21"/"D100" gibi doğal adres girilirse, ham Modbus adresini elle
    hesaplamak yerine otomatik çözülsün. Profil/doğal adres verilmezse eski
    davranış (elle girilen ham modbus_adres) aynen çalışmaya devam eder.
    Döner: (modbus_adres, plc_profil_id, dogal_adres, hata)."""
    dogal_adres = str(veri.get('dogal_adres', '') or '').strip()
    plc_profil_ham = veri.get('plc_profil_id')
    plc_profil_id = int(plc_profil_ham) if plc_profil_ham not in (None, '') else None
    if not dogal_adres or not plc_profil_id:
        return str(veri.get('modbus_adres', mevcut_modbus_adres)).strip(), plc_profil_id, (dogal_adres or None), None
    ok, sonuc = database.dogal_adresi_coz(plc_profil_id, dogal_adres)
    if not ok:
        return None, None, None, sonuc
    return str(sonuc['ham_adres']), plc_profil_id, dogal_adres, None


@dashboard_bp.route('/cihaz/<int:cihaz_id>/tag/<int:tag_id>/alan-guncelle', methods=['POST'])
@tasarimci_required
def tag_alan_guncelle(cihaz_id, tag_id):
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    mevcut = database.tag_getir(tag_id)
    if not mevcut or mevcut['cihaz_id'] != cihaz_id:
        return jsonify({'error': 'Tag bulunamadı'}), 404
    veri = request.get_json(silent=True) or {}
    ad = str(veri.get('ad', mevcut['ad'])).strip()
    veri_tipi = veri.get('veri_tipi', mevcut['veri_tipi'])
    erisim = veri.get('erisim', mevcut['erisim'])
    modbus_adres, plc_profil_id, dogal_adres, adres_hata = _dogal_adres_uygula(veri, mevcut['modbus_adres'])
    if adres_hata:
        return jsonify({'error': adres_hata}), 400
    if not ad or not modbus_adres:
        return jsonify({'error': 'Tag adı ve Modbus adresi boş olamaz'}), 400
    gecmis_ham = veri.get('gecmis_araligi_sn', mevcut.get('gecmis_araligi_sn'))
    try:
        gecmis_araligi_sn = int(gecmis_ham) if gecmis_ham not in (None, '') else None
        if gecmis_araligi_sn is not None and gecmis_araligi_sn < 1:
            return jsonify({'error': 'Kayıt aralığı en az 1 saniye olmalı'}), 400
    except (TypeError, ValueError):
        return jsonify({'error': 'Kayıt aralığı sayısal olmalı'}), 400
    gecmis_kayit_aktif = veri.get('gecmis_kayit_aktif', bool(mevcut.get('gecmis_kayit_aktif')))
    ok, hata = database.tag_guncelle(tag_id, ad, modbus_adres, veri_tipi, erisim, gecmis_araligi_sn, gecmis_kayit_aktif,
                                      plc_profil_id, dogal_adres)
    if not ok:
        return jsonify({'error': hata}), 400
    return jsonify({'success': True, 'modbus_adres': modbus_adres})


@dashboard_bp.route('/cihaz/<int:cihaz_id>/tag-ekle-json', methods=['POST'])
@tasarimci_required
def tag_ekle_json(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    veri = request.get_json(silent=True) or {}
    ad = str(veri.get('ad', '')).strip()
    veri_tipi = veri.get('veri_tipi') or 'bool'
    erisim = veri.get('erisim') or 'read'
    modbus_adres, plc_profil_id, dogal_adres, adres_hata = _dogal_adres_uygula(veri, '')
    if adres_hata:
        return jsonify({'error': adres_hata}), 400
    if not ad or not modbus_adres:
        return jsonify({'error': 'Tag adı ve Modbus adresi zorunlu'}), 400
    gecmis_ham = veri.get('gecmis_araligi_sn')
    try:
        gecmis_araligi_sn = int(gecmis_ham) if gecmis_ham not in (None, '') else None
        if gecmis_araligi_sn is not None and gecmis_araligi_sn < 1:
            return jsonify({'error': 'Kayıt aralığı en az 1 saniye olmalı'}), 400
    except (TypeError, ValueError):
        return jsonify({'error': 'Kayıt aralığı sayısal olmalı'}), 400
    gecmis_kayit_aktif = bool(veri.get('gecmis_kayit_aktif'))
    ok, sonuc = database.tag_ekle(cihaz_id, ad, modbus_adres, veri_tipi, erisim, gecmis_araligi_sn, gecmis_kayit_aktif,
                                   plc_profil_id, dogal_adres)
    if not ok:
        return jsonify({'error': sonuc}), 400
    return jsonify({'success': True, 'id': sonuc, 'modbus_adres': modbus_adres})


@dashboard_bp.route('/cihaz/<int:cihaz_id>/tag/<int:tag_id>/sil-json', methods=['POST'])
@tasarimci_required
def tag_sil_json(cihaz_id, tag_id):
    if not _cihaz_dogrula(cihaz_id):
        return jsonify({'error': 'Cihaz bulunamadı'}), 404
    mevcut = database.tag_getir(tag_id)
    if not mevcut or mevcut['cihaz_id'] != cihaz_id:
        return jsonify({'error': 'Tag bulunamadı'}), 404
    database.tag_sil(tag_id)
    return jsonify({'success': True})


# ============================================================
# FIRMWARE (ESP32 uzaktan güncelleme)
# ============================================================

@dashboard_bp.route('/cihaz/<int:cihaz_id>/firmware-yukle', methods=['POST'])
@tasarimci_required
def firmware_yukle(cihaz_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))

    dosya = request.files.get('firmware_dosya')
    if not dosya or dosya.filename == '':
        flash('Dosya seçilmedi.', 'danger')
        return redirect(url_for('dashboard.cihaz_firmware', cihaz_id=cihaz_id))
    if not dosya.filename.lower().endswith('.bin'):
        flash('Sadece .bin dosyaları yüklenebilir.', 'danger')
        return redirect(url_for('dashboard.cihaz_firmware', cihaz_id=cihaz_id))

    veri = dosya.read()
    if len(veri) > 4 * 1024 * 1024:
        flash('Dosya çok büyük (maksimum 4 MB).', 'danger')
        return redirect(url_for('dashboard.cihaz_firmware', cihaz_id=cihaz_id))

    versiyon = request.form.get('versiyon', '').strip() or '1.0.0'
    aciklama = request.form.get('aciklama', '').strip()
    hedef = request.form.get('hedef', 'bu_cihaz')
    hedef_cihaz_id = None if hedef == 'tum_cihazlar' else cihaz_id

    ok, sonuc = database.firmware_yukle(session['proje_id'], hedef_cihaz_id, dosya.filename, veri, versiyon, aciklama)
    if ok:
        hedef_yazi = 'projedeki tüm cihazlara' if hedef_cihaz_id is None else 'bu cihaza'
        flash(f'✅ Firmware yüklendi ({hedef_yazi} uygulanacak): {dosya.filename}', 'success')
    else:
        flash(f'Hata: {sonuc}', 'danger')
    return redirect(url_for('dashboard.cihaz_firmware', cihaz_id=cihaz_id))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/firmware/<int:firmware_id>/aktiflik', methods=['POST'])
@tasarimci_required
def firmware_aktiflik(cihaz_id, firmware_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    fw = database.firmware_getir(firmware_id)
    if not fw or fw['proje_id'] != session['proje_id']:
        flash('Firmware bulunamadı.', 'danger')
        return redirect(url_for('dashboard.cihaz_firmware', cihaz_id=cihaz_id))
    yeni_aktif = not fw['aktif']
    database.firmware_aktiflik_ayarla(firmware_id, yeni_aktif)
    flash('Firmware aktifleştirildi — hedef cihaz(lar) bir sonraki kontrolde indirecek.' if yeni_aktif else 'Firmware pasif yapıldı.', 'success')
    return redirect(url_for('dashboard.cihaz_firmware', cihaz_id=cihaz_id))


@dashboard_bp.route('/cihaz/<int:cihaz_id>/firmware/<int:firmware_id>/sil', methods=['POST'])
@tasarimci_required
def firmware_sil(cihaz_id, firmware_id):
    if not _cihaz_dogrula(cihaz_id):
        flash('Cihaz bulunamadı.', 'danger')
        return redirect(url_for('dashboard.dashboard_page'))
    fw = database.firmware_getir(firmware_id)
    if not fw or fw['proje_id'] != session['proje_id']:
        flash('Firmware bulunamadı.', 'danger')
        return redirect(url_for('dashboard.cihaz_firmware', cihaz_id=cihaz_id))
    database.firmware_sil(firmware_id)
    flash('Firmware silindi.', 'success')
    return redirect(url_for('dashboard.cihaz_firmware', cihaz_id=cihaz_id))
