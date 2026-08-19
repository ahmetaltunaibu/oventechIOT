from flask import Flask, redirect, url_for, session
import os
import database
from pages.login import login_bp
from pages.dashboard import dashboard_bp

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-degistir')
app.config['PERMANENT_SESSION_LIFETIME'] = 28800

database.init_db()

app.register_blueprint(login_bp)
app.register_blueprint(dashboard_bp)


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
