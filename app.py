from flask import Flask, redirect, url_for, session
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
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response


@app.route('/ping')
def ping():
    return {'ok': True}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=True)
