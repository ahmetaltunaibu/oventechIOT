"""Tüm veritabanının (tüm projeler) yedeğini indirme/geri yükleme.

Render'ın diski deploy'da sıfırlandığı için (Postgres'e geçene kadar geçici
çözüm): deploy ÖNCESİ buradan yedek indirilir, deploy SONRASI aynı yerden
geri yüklenir. Normal proje girişinden BAĞIMSIZ, platform seviyesinde tek
bir gizli anahtarla korunuyor — proje kullanıcılarının (tasarımcı dahil)
bununla hiç ilgisi yok, sadece siteyi işleten kişi (sen) kullanır.
"""
import os
from flask import Blueprint, request, send_file, jsonify, render_template_string
import database

yedekleme_bp = Blueprint('yedekleme', __name__)

PLATFORM_ADMIN_KEY = os.environ.get('PLATFORM_ADMIN_KEY')

_SAYFA_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Yedekleme - OventechIOT</title>
    <style>
        body { font-family: -apple-system, Roboto, sans-serif; background:#0f1720; color:#e8eef4; margin:0; padding:24px; }
        .card { max-width:420px; margin:20px auto; background:#17222e; border:1px solid #2a3b4c; border-radius:10px; padding:20px; }
        h2 { font-size:16px; margin-bottom:14px; color:#2e9ed9; }
        label { font-size:12px; color:#8aa0b3; display:block; margin-bottom:4px; }
        input { width:100%; padding:9px 12px; background:#1e2d3d; border:1px solid #2a3b4c; border-radius:6px; color:#e8eef4; font-size:14px; margin-bottom:12px; box-sizing:border-box; }
        button, a.btn { background:#2e9ed9; color:#fff; border:none; padding:9px 16px; border-radius:6px; font-size:14px; font-weight:600; cursor:pointer; text-decoration:none; display:inline-block; }
        #sonuc { margin-top:10px; font-size:13px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>📥 Yedek İndir</h2>
        <label>Platform Anahtarı</label>
        <input type="password" id="anahtarIndir">
        <button onclick="indir()">İndir</button>
    </div>

    <div class="card">
        <h2>📤 Yedek Yükle (Geri Yükle)</h2>
        <label>Platform Anahtarı</label>
        <input type="password" id="anahtarYukle">
        <label>Yedek Dosyası (.db)</label>
        <input type="file" id="dosya" accept=".db">
        <button onclick="yukle()">Yükle</button>
        <div id="sonuc"></div>
    </div>

    <script>
    function indir() {
        const anahtar = document.getElementById('anahtarIndir').value.trim();
        if (!anahtar) { alert('Anahtar gerekli.'); return; }
        window.location.href = '/yedek/indir?anahtar=' + encodeURIComponent(anahtar);
    }

    async function yukle() {
        const anahtar = document.getElementById('anahtarYukle').value.trim();
        const dosyaInput = document.getElementById('dosya');
        const sonucEl = document.getElementById('sonuc');
        if (!anahtar) { alert('Anahtar gerekli.'); return; }
        if (!dosyaInput.files.length) { alert('Dosya seç.'); return; }

        const fd = new FormData();
        fd.append('dosya', dosyaInput.files[0]);
        fd.append('anahtar', anahtar);

        sonucEl.textContent = 'Yükleniyor...';
        try {
            const res = await fetch('/yedek/yukle', { method: 'POST', body: fd });
            const data = await res.json();
            sonucEl.textContent = res.ok ? ('✅ ' + data.mesaj) : ('❌ ' + (data.error || 'Hata'));
        } catch (e) {
            sonucEl.textContent = '❌ İstek başarısız: ' + e;
        }
    }
    </script>
</body>
</html>
"""


def _anahtar_dogru_mu():
    verilen = request.args.get('anahtar') or request.form.get('anahtar') or request.headers.get('X-Platform-Key')
    return PLATFORM_ADMIN_KEY and verilen == PLATFORM_ADMIN_KEY


@yedekleme_bp.route('/yedek')
def yedek_sayfasi():
    return render_template_string(_SAYFA_HTML)


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
