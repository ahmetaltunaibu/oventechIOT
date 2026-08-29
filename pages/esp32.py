"""ESP32 köprü cihazının sunucuyla konuştuğu uçlar.

Bu uçların kullanıcı oturumuyla (session) hiçbir ilgisi yok — ESP32 tarayıcı
değil, kimliğini URL'deki `cihaz_kimlik` (cihaz eklenirken tek seferlik
üretilen, tahmin edilemez bir token) ile kanıtlıyor. Akış:

1) ESP32 açılışta (ve periyodik olarak, tag listesi değişmiş olabilir diye)
   GET /esp32/<cihaz_kimlik>/tagler ile hangi tag'leri hangi Modbus adresinden
   okuyup/yazacağını öğrenir.
2) ESP32 kendi döngüsünde (PLC'den Modbus RTU/TCP ile okuduğu değerlerle)
   periyodik olarak POST /esp32/<cihaz_kimlik>/xchange çağırır:
   - Body: {"degerler": {"<tag_id>": "<okunan_deger>", ...}}
   - Cevap: {"yazilacaklar": {"<tag_id>": "<yazilacak_deger>", ...}}
     (kullanıcının panelden yazdığı, henüz PLC'ye iletilmemiş değerler)
   Sunucu her gelen değeri kaydeder (deger + tag_deger_gecmis, grafik için)
   VE o tag'e bağlı alarm kuralları varsa değerlendirir (database.alarm_degerlendir)
   — böylece tarayıcı hiç açık olmasa bile alarm sunucuda oluşur/kapanır.
"""
from flask import Blueprint, request, jsonify, Response
import database

esp32_bp = Blueprint('esp32', __name__)


@esp32_bp.route('/esp32/<cihaz_kimlik>/tagler')
def esp32_tagler(cihaz_kimlik):
    cihaz = database.cihaz_getir_kimlik(cihaz_kimlik)
    if not cihaz:
        return jsonify({'error': 'Geçersiz cihaz kimliği'}), 404
    database.cihaz_son_gorulme_guncelle(cihaz_kimlik)
    tagler = database.cihaz_tagleri(cihaz['id'])
    return jsonify({
        'cihaz_id': cihaz['id'],
        'cihaz_ad': cihaz['ad'],
        'tagler': [
            {
                'id': t['id'], 'ad': t['ad'], 'modbus_adres': t['modbus_adres'],
                'veri_tipi': t['veri_tipi'], 'erisim': t['erisim'],
            }
            for t in tagler
        ],
    })


@esp32_bp.route('/esp32/<cihaz_kimlik>/yazilacaklar')
def esp32_yazilacaklar(cihaz_kimlik):
    """KRİTİK (kullanıcı raporu — buton/switch'e basınca önce doğru olup
    kısa süreliğine eski değere dönüp tekrar düzeliyordu): ESP32'nin her
    döngüsü ÖNCE PLC'den okuyup o (henüz yazma uygulanmamış) değerleri
    sunucuya bildiriyor, SONRA sunucudan gelen yazma isteğini PLC'ye
    yazıyordu — hepsi AYNI döngüde. Yani bir yazma isteği en erken BİR
    SONRAKİ döngüde PLC'ye uygulanıyor, o yazmanın PLC'ye gerçekten
    yansıdığı okuma ise ONDAN SONRAKİ döngüde bildiriliyordu — en kötü
    ihtimalle ~2 tam döngü (10sn) gecikme.

    Bu uç, firmware'in döngü sırasını tersine çevirmesini sağlıyor: ESP32
    artık PLC'den okumadan ÖNCE burayı çağırıp yazılacakları hemen
    uyguluyor, sonra okuyup gönderiyor — böylece o döngüde okunan/bildirilen
    değer ZATEN yeni (yazılmış) değeri yansıtıyor, gecikme tek döngüye
    (~5sn) iniyor. /xchange'in yanıtındaki 'yazilacaklar' de duruyor
    (geriye dönük uyumluluk + burayla POST arasında araya giren bir yazma
    için ek güvenlik ağı) — aynı tag burada zaten temizlendiği için
    /xchange normalde onu bir daha döndürmez, çift yazma riski yok."""
    cihaz = database.cihaz_getir_kimlik(cihaz_kimlik)
    if not cihaz:
        return jsonify({'error': 'Geçersiz cihaz kimliği'}), 404
    yazilacaklar = {}
    yazilan_tag_idleri = []
    for t in database.cihaz_tagleri(cihaz['id']):
        if t['erisim'] in ('write', 'readwrite') and t.get('yazilacak_deger') not in (None, ''):
            yazilacaklar[str(t['id'])] = t['yazilacak_deger']
            yazilan_tag_idleri.append(t['id'])
    if yazilan_tag_idleri:
        database.tagler_yazilacak_temizle(yazilan_tag_idleri)
    return jsonify({'yazilacaklar': yazilacaklar})


@esp32_bp.route('/esp32/<cihaz_kimlik>/xchange', methods=['POST'])
def esp32_xchange(cihaz_kimlik):
    cihaz = database.cihaz_getir_kimlik(cihaz_kimlik)
    if not cihaz:
        return jsonify({'error': 'Geçersiz cihaz kimliği'}), 404
    database.cihaz_son_gorulme_guncelle(cihaz_kimlik)

    veri = request.get_json(silent=True) or {}
    degerler = veri.get('degerler') or {}
    if not isinstance(degerler, dict):
        return jsonify({'error': 'degerler bir obje olmalı: {"tag_id": "deger"}'}), 400

    # Kullanıcı isteği: ESP32'nin bu turdaki Modbus okuma/yazma sağlığını
    # (eskiden sadece Seri Monitör'de görünen bilgi) sunucuya da bildirsin —
    # cihaz yönetim sayfasında görülebilsin. Alanlar opsiyonel — eski
    # firmware (bunları göndermeyen) hâlâ sorunsuz çalışır.
    if 'modbus_saglikli' in veri:
        database.cihaz_modbus_durumu_guncelle(
            cihaz_kimlik,
            bool(veri.get('modbus_saglikli')),
            int(veri.get('modbus_hata_sayisi') or 0),
            str(veri.get('modbus_hata_mesaji') or '')[:500]
        )

    # Kullanıcı isteği: WiFi sinyal gücü — alan opsiyonel, eski firmware
    # (bunu göndermeyen) hâlâ sorunsuz çalışır.
    if 'wifi_rssi' in veri:
        try:
            database.cihaz_wifi_rssi_guncelle(cihaz_kimlik, int(veri.get('wifi_rssi')))
        except (TypeError, ValueError):
            pass

    tagler = database.cihaz_tagleri(cihaz['id'])
    tag_idleri = {t['id'] for t in tagler}
    islenen = 0
    for tag_id_str, deger in degerler.items():
        try:
            tag_id = int(tag_id_str)
        except (TypeError, ValueError):
            continue
        if tag_id not in tag_idleri:
            continue  # başka cihaza ait ya da silinmiş tag — sessizce atla
        database.tag_deger_guncelle(tag_id, deger)
        islenen += 1

    # Kullanıcının panelden yazdığı (henüz PLC'ye iletilmemiş) değerler.
    #
    # KRİTİK (kullanıcı raporu): PLC'yi SCADA'nın yanı sıra kendi fiziksel
    # HMI paneli de kontrol ediyor. yazilacak_deger önceden burada hiç
    # temizlenmiyordu — bu yüzden ESP32 aynı değeri HER xchange döngüsünde
    # (5sn'de bir) tekrar tekrar PLC'ye zorla yazıyor, HMI'den yapılan
    # değişiklikleri sürekli eziyordu. Artık: bir tag'in yazilacak_deger'i
    # bu yanıtla ESP32'ye gönderilir gönderilmez temizleniyor — SCADA bir
    # tag'i SADECE BİR KEZ yazar, sonraki bir panel işlemine kadar sadece
    # okur (HMI'nin/PLC'nin kendi değişikliklerine karışmaz).
    yazilacaklar = {}
    yazilan_tag_idleri = []
    for t in database.cihaz_tagleri(cihaz['id']):
        if t['erisim'] in ('write', 'readwrite') and t.get('yazilacak_deger') not in (None, ''):
            yazilacaklar[str(t['id'])] = t['yazilacak_deger']
            yazilan_tag_idleri.append(t['id'])
    if yazilan_tag_idleri:
        database.tagler_yazilacak_temizle(yazilan_tag_idleri)

    # Kullanıcı isteği: cihaz yönetim sayfasındaki "WiFi Ayarlarını Uzaktan
    # Sıfırla" butonuna basıldıysa, ESP32 hâlâ bağlıyken (bu isteği
    # yapabildiğine göre) burada haber veriliyor — bayrak tek seferlik,
    # okunur okunmaz sıfırlanıyor.
    wifi_sifirla = database.cihaz_wifi_sifirlama_durumu_al_ve_temizle(cihaz_kimlik)

    return jsonify({'success': True, 'islenen': islenen, 'yazilacaklar': yazilacaklar, 'wifi_sifirla': wifi_sifirla})


# ============================================================
# UZAKTAN GÜNCELLEME (OTA) — gemba/gemba-iot-gateway'deki çalışan sistemle
# aynı akış: kontrol et -> güncelleme varsa indir -> başarılıyı bildir.
# ============================================================

@esp32_bp.route('/esp32/<cihaz_kimlik>/firmware/kontrol')
def esp32_firmware_kontrol(cihaz_kimlik):
    cihaz = database.cihaz_getir_kimlik(cihaz_kimlik)
    if not cihaz:
        return jsonify({'error': 'Geçersiz cihaz kimliği'}), 404
    mevcut_versiyon = request.args.get('version', '').strip()
    fw = database.firmware_kontrol(cihaz['id'], mevcut_versiyon)
    if not fw:
        return jsonify({'update_available': False, 'current_version': mevcut_versiyon or '?'})

    database.firmware_gecmis_kaydet(cihaz['id'], fw['id'], 'indiriliyor')
    indirme_url = f"{request.url_root.rstrip('/')}/esp32/{cihaz_kimlik}/firmware/indir/{fw['id']}"
    return jsonify({
        'update_available': True,
        'current_version': mevcut_versiyon or '?',
        'latest_version': fw['versiyon'],
        'firmware_id': fw['id'],
        'firmware_url': indirme_url,
        'firmware_filename': fw['dosya_adi'],
        'file_size': fw['boyut'],
        'md5_hash': fw['md5_hash'],
        'release_notes': fw.get('aciklama') or '',
    })


@esp32_bp.route('/esp32/<cihaz_kimlik>/firmware/indir/<int:firmware_id>')
def esp32_firmware_indir(cihaz_kimlik, firmware_id):
    cihaz = database.cihaz_getir_kimlik(cihaz_kimlik)
    if not cihaz:
        return jsonify({'error': 'Geçersiz cihaz kimliği'}), 404
    fw = database.firmware_getir(firmware_id)
    if not fw or fw['proje_id'] != cihaz['proje_id']:
        return jsonify({'error': 'Firmware bulunamadı'}), 404
    return Response(
        fw['veri'], mimetype='application/octet-stream',
        headers={'Content-Disposition': f'attachment; filename="{fw["dosya_adi"]}"'}
    )


@esp32_bp.route('/esp32/<cihaz_kimlik>/firmware/basarili', methods=['POST'])
def esp32_firmware_basarili(cihaz_kimlik):
    cihaz = database.cihaz_getir_kimlik(cihaz_kimlik)
    if not cihaz:
        return jsonify({'error': 'Geçersiz cihaz kimliği'}), 404
    veri = request.get_json(silent=True) or {}
    firmware_id = veri.get('firmware_id')
    if not firmware_id:
        return jsonify({'error': 'firmware_id gerekli'}), 400
    database.firmware_gecmis_kaydet(cihaz['id'], firmware_id, 'basarili')
    return jsonify({'success': True})
