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
from flask import Blueprint, request, jsonify
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
    yazilacaklar = {}
    for t in database.cihaz_tagleri(cihaz['id']):
        if t['erisim'] in ('write', 'readwrite') and t.get('yazilacak_deger') not in (None, ''):
            yazilacaklar[str(t['id'])] = t['yazilacak_deger']

    return jsonify({'success': True, 'islenen': islenen, 'yazilacaklar': yazilacaklar})
