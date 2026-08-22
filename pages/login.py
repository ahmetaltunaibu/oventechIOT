from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from functools import wraps
import database

login_bp = Blueprint('login', __name__)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('platform_admin'):
            return f(*args, **kwargs)
        if 'kullanici_id' not in session:
            flash('Lütfen giriş yapın!', 'warning')
            return redirect(url_for('login.login_page'))
        return f(*args, **kwargs)
    return decorated


def tasarimci_required(f):
    """Sadece 'tasarimci' rolündeki kullanıcılar (ya da platform admin)
    sayfa/element düzenleyebilir."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('platform_admin'):
            return f(*args, **kwargs)
        if 'kullanici_id' not in session:
            flash('Lütfen giriş yapın!', 'warning')
            return redirect(url_for('login.login_page'))
        if session.get('rol') != 'tasarimci':
            flash('Bu işlem için tasarımcı yetkisi gerekiyor.', 'danger')
            return redirect(url_for('dashboard.dashboard_page'))
        return f(*args, **kwargs)
    return decorated


@login_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        kullanici_adi = request.form.get('kullanici_adi', '')
        sifre         = request.form.get('sifre', '')

        proje, kullanici = database.giris_dogrula(kullanici_adi, sifre)
        if not kullanici:
            flash('Kullanıcı adı ya da şifre hatalı.', 'danger')
            return redirect(url_for('login.login_page'))

        session.clear()
        session.permanent = True  # PERMANENT_SESSION_LIFETIME'a (8 saat) bağlı, kalıcı bir çerez olsun
        session['kullanici_id']  = kullanici['id']
        session['kullanici_adi'] = kullanici['kullanici_adi']
        session['ad_soyad']      = kullanici['ad_soyad']
        session['rol']           = kullanici['rol']
        session['proje_id']      = proje['id'] if proje else None
        session['proje_ad']      = proje['ad'] if proje else None
        return redirect(url_for('dashboard.dashboard_page'))

    return render_template('login.html')


@login_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login.login_page'))
