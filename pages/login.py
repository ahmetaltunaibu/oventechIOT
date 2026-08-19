from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from functools import wraps
import database

login_bp = Blueprint('login', __name__)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'kullanici_id' not in session:
            flash('Lütfen giriş yapın!', 'warning')
            return redirect(url_for('login.login_page'))
        return f(*args, **kwargs)
    return decorated


def tasarimci_required(f):
    """Sadece 'tasarimci' rolündeki kullanıcılar sayfa/element düzenleyebilir."""
    @wraps(f)
    def decorated(*args, **kwargs):
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
        proje_kodu    = request.form.get('proje_kodu', '')
        kullanici_adi = request.form.get('kullanici_adi', '')
        sifre         = request.form.get('sifre', '')

        proje, kullanici = database.giris_dogrula(proje_kodu, kullanici_adi, sifre)
        if not proje or not kullanici:
            flash('Proje kodu, kullanıcı adı ya da şifre hatalı.', 'danger')
            return redirect(url_for('login.login_page'))

        session['kullanici_id'] = kullanici['id']
        session['kullanici_adi'] = kullanici['kullanici_adi']
        session['ad_soyad']     = kullanici['ad_soyad']
        session['rol']          = kullanici['rol']
        session['proje_id']     = proje['id']
        session['proje_kodu']   = proje['kod']
        session['proje_ad']     = proje['ad']
        return redirect(url_for('dashboard.dashboard_page'))

    return render_template('login.html')


@login_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login.login_page'))


@login_bp.route('/ilk-kurulum', methods=['GET', 'POST'])
def ilk_kurulum():
    """Veritabanında HİÇ proje yokken çalışır — ilk projeyi/tasarımcı
    kullanıcısını oluşturur. Bir proje oluşturulduktan sonra bu sayfa
    kendini kilitler (bir daha asla çalışmaz), o yüzden herkese açık
    olması güvenlik riski değil."""
    if database.hic_proje_var_mi():
        flash('Kurulum zaten tamamlanmış — bu sayfa artık kullanılamaz.', 'warning')
        return redirect(url_for('login.login_page'))

    if request.method == 'POST':
        proje_kodu    = request.form.get('proje_kodu', '').strip()
        proje_ad      = request.form.get('proje_ad', '').strip()
        kullanici_adi = request.form.get('kullanici_adi', '').strip()
        sifre         = request.form.get('sifre', '')
        ad_soyad      = request.form.get('ad_soyad', '').strip()

        if not all([proje_kodu, proje_ad, kullanici_adi, sifre, ad_soyad]):
            flash('Tüm alanlar zorunlu.', 'danger')
            return redirect(url_for('login.ilk_kurulum'))

        ok, sonuc = database.proje_ekle(proje_kodu, proje_ad)
        if not ok:
            flash(f'Hata: {sonuc}', 'danger')
            return redirect(url_for('login.ilk_kurulum'))

        ok2, sonuc2 = database.kullanici_ekle(sonuc, kullanici_adi, sifre, ad_soyad, rol='tasarimci')
        if not ok2:
            flash(f'Hata: {sonuc2}', 'danger')
            return redirect(url_for('login.ilk_kurulum'))

        flash('İlk proje ve tasarımcı kullanıcı oluşturuldu — giriş yapabilirsin.', 'success')
        return redirect(url_for('login.login_page'))

    return render_template('ilk_kurulum.html')
