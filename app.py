from flask import Flask, redirect, url_for, session, request
import os
import database
from pages.login import login_bp
from pages.dashboard import dashboard_bp
from pages.yedekleme import yedekleme_bp
from pages.admin import admin_bp
from pages.sayfa import sayfa_bp
from pages.esp32 import esp32_bp

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-degistir')
app.config['PERMANENT_SESSION_LIFETIME'] = 28800

database.init_db()
database.demo_projesini_sil()

app.register_blueprint(login_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(yedekleme_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(sayfa_bp)
app.register_blueprint(esp32_bp)


@app.after_request
def no_cache(response):
    # Kullanıcı raporu: sayfa geçişlerinde gecikme + grafik elementinin
    # "önce boş, sonra dolu" iki aşamalı görünmesi. Kök neden: bu kural
    # /static/ altındaki dosyalara da (örn. ApexCharts kütüphanesi, ~540KB)
    # uygulanıyordu — tarayıcı hiç önbelleğe alamıyor, HER sayfa geçişinde
    # sıfırdan indiriyordu. Canlı SCADA verisi (tag değerleri, sayfa JSON'u,
    # API cevapları vb.) için no-cache KESİNLİKLE doğru/gerekli — ama
    # statik dosyalar (JS/CSS, neredeyse hiç değişmiyor) bunun dışında
    # tutulmalı, Flask'ın kendi varsayılan (makul) önbellek başlığı geçerli
    # olsun diye burada erken dönülüyor.
    if request.path.startswith('/static/'):
        return response
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response


@app.route('/ping')
def ping():
    return {'ok': True}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=True)
