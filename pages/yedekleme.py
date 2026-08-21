"""Tüm veritabanının (tüm projeler) yedeğini indirme/geri yükleme.

Render'ın diski deploy'da sıfırlandığı için (Postgres'e geçene kadar geçici
çözüm): deploy ÖNCESİ buradan yedek indirilir, deploy SONRASI aynı yerden
geri yüklenir. Normal proje girişinden BAĞIMSIZ, platform seviyesinde tek
bir gizli anahtarla korunuyor — proje kullanıcılarının (tasarımcı dahil)
bununla hiç ilgisi yok, sadece siteyi işleten kişi (sen) kullanır.

Sayfa artık base.html temasını kullanıyor ve admin panelinden (üst menüdeki
"💾 Yedekleme" linki) erişilebiliyor — önceden bağımsız/gizli bir sayfaydı.
"""
import os
from flask import Blueprint, request, send_file, jsonify, render_template
import database

yedekleme_bp = Blueprint('yedekleme', __name__)

PLATFORM_ADMIN_KEY = os.environ.get('PLATFORM_ADMIN_KEY')


def _anahtar_dogru_mu():
    verilen = request.args.get('anahtar') or request.form.get('anahtar') or request.headers.get('X-Platform-Key')
    return PLATFORM_ADMIN_KEY and verilen == PLATFORM_ADMIN_KEY


@yedekleme_bp.route('/yedek')
def yedek_sayfasi():
    return render_template('yedekleme.html')


@yedekleme_bp.route('/yedek/indir')
def yedek_indir():
    if not _anahtar_dogru_mu():
        return jsonify({'error': 'Unauthorized'}), 401
    if not os.path.exists(database.DB_NAME):
        return jsonify({'error': 'Veritabanı dosyası henüz yok'}), 404
    return send_file(database.DB_NAME, as_attachment=True, download_name='oventechiot_yedek.db')


@yedekleme_bp.route('/yedek/yukle', methods=['POST'])
def yedek_yukle():
    if not _anahtar_dogru_mu():
        return jsonify({'error': 'Unauthorized'}), 401
    dosya = request.files.get('dosya')
    if not dosya or dosya.filename == '':
        return jsonify({'error': 'Dosya gönderilmedi (form alanı adı: dosya)'}), 400
    dosya.save(database.DB_NAME)
    return jsonify({'success': True, 'mesaj': 'Veritabanı geri yüklendi'})
