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
    # KRİTİK: WAL modunda son yazılan satırlar bazen ana .db dosyasına değil
    # yanındaki .db-wal dosyasına yazılı kalır (checkpoint olana kadar).
    # Ana dosyayı doğrudan kopyalayıp göndermek bu yüzden GÜNCEL OLMAYAN bir
    # yedek verebilirdi — bu bug'ın bulunma sebebi tam olarak buydu (yedek
    # alındığı anda gerçekte var olan bir cihaz, yedekte hiç görünmüyordu).
    # Göndermeden hemen önce WAL'ı ana dosyaya "checkpoint" ile taşıyoruz.
    conn = database.get_db()
    try:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.commit()
    finally:
        conn.close()
    return send_file(database.DB_NAME, as_attachment=True, download_name='oventechiot_yedek.db')


@yedekleme_bp.route('/yedek/yukle', methods=['POST'])
def yedek_yukle():
    if not _anahtar_dogru_mu():
        return jsonify({'error': 'Unauthorized'}), 401
    dosya = request.files.get('dosya')
    if not dosya or dosya.filename == '':
        return jsonify({'error': 'Dosya gönderilmedi (form alanı adı: dosya)'}), 400
    dosya.save(database.DB_NAME)
    # Eski (yüklemeden önceki) çalışmadan kalmış -wal/-shm yan dosyaları
    # varsa sil — yoksa SQLite yeni yüklenen dosyayı açarken eski WAL
    # içeriğini "kurtarmaya" çalışıp yanlışlıkla eski verilerle
    # karıştırabilir.
    for uzanti in ('-wal', '-shm'):
        yan_dosya = database.DB_NAME + uzanti
        if os.path.exists(yan_dosya):
            os.remove(yan_dosya)
    # NOT (bulunan hata): yüklenen yedek dosyası ESKİ bir şemaya sahip
    # olabilir (kod ilerlemiş, migration'lar eklenmiş ama yedek eski
    # tarihli) — init_db() normalde sadece uygulama açılışında bir kez
    # çalıştığı için, geri yükleme sırasında yeni dosyaya migration
    # UYGULANMIYORDU (örn. "no such column: nav_stili" hatası buradan
    # geliyordu). Yüklemeden hemen sonra migration'ları bu dosya üzerinde
    # tekrar çalıştırıyoruz.
    database.init_db()
    return jsonify({'success': True, 'mesaj': 'Veritabanı geri yüklendi ve güncel şemaya taşındı'})
