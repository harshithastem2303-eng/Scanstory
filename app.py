import os
import time
import mimetypes
import threading
import json
import uuid
import razorpay
from functools import lru_cache, wraps
from datetime import datetime as dt, timedelta
from flask import (
    Flask, request, redirect, url_for, session,
    jsonify, flash, send_from_directory, render_template, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
import cv2
import numpy as np
import qrcode
from qrcode.image.styledpil import StyledPilImage
from PIL import Image, ImageFile
import ffmpeg
import secrets
import hashlib
import traceback
from concurrent.futures import ThreadPoolExecutor


from sqlalchemy import or_, desc, func, and_, case

# ✅ Import models
from models import (
    db, User, Admin, SubscriptionPlan, TrialDetails, OTPCode,
    Project, ProjectPair, PaymentOrder, ScanLog, SystemConfig,
    UserLoginActivity, AdminActivity
)


# --------------------------------------------------------------------------------------------
# Flask / DB config WITH POOLING
# --------------------------------------------------------------------------------------------
load_dotenv()
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ✅ ADD THESE 2 LINES TO DISABLE CSRF
app.config['WTF_CSRF_ENABLED'] = False
app.config['WTF_CSRF_CHECK_DEFAULT'] = False

# Secret key (only set once)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# ✅ ADD DATABASE CONFIGURATION HERE
app.config.update(
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", ""),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={
        'pool_size': 10,
        'max_overflow': 20,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'pool_timeout': 30,
        'connect_args': {
            'connect_timeout': 10,
            'charset': 'utf8mb4'
        }
    }
)

# ✅ Initialize SQLAlchemy ONLY ONCE
db.init_app(app)

# Ensure correct MIME type for wasm
mimetypes.add_type("application/wasm", ".wasm")
ImageFile.LOAD_TRUNCATED_IMAGES = True


# --------------------------------------------------------------------------------------------
# Razorpay Configuration
# --------------------------------------------------------------------------------------------
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# Initialize Razorpay client with proper error handling
try:
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
        razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        print("✅ Razorpay client initialized successfully")
    else:
        razorpay_client = None
        print("⚠️ Razorpay keys not configured. Payments will not work.")
except Exception as e:
    razorpay_client = None
    print(f"❌ Razorpay initialization failed: {e}")

# --------------------------------------------------------------------------------------------
# Storage paths
# --------------------------------------------------------------------------------------------
DATA_DIR = "data"
IMAGES_DIR = os.path.join(DATA_DIR, "images")
VIDEOS_DIR = os.path.join(DATA_DIR, "videos")
FEATURES_DIR = os.path.join(DATA_DIR, "features")
QR_DIR = os.path.join(DATA_DIR, "qr_codes")
STATIC_UPLOADS_DIR = os.path.join("static", "uploads")
STATIC_JS_DIR = os.path.join("static", "js")
LOGOS_DIR = os.path.join(STATIC_UPLOADS_DIR, "logos")
ADMIN_UPLOADS_DIR = os.path.join(STATIC_UPLOADS_DIR, "admin")

for d in (DATA_DIR, IMAGES_DIR, VIDEOS_DIR, FEATURES_DIR, QR_DIR, STATIC_UPLOADS_DIR, STATIC_JS_DIR, LOGOS_DIR, ADMIN_UPLOADS_DIR):
    os.makedirs(d, exist_ok=True)

ADMIN_DATA_DIR = os.path.join(BASE_DIR, "data_admin")
ADMIN_IMAGES_DIR = os.path.join(ADMIN_DATA_DIR, "images")
ADMIN_VIDEOS_DIR = os.path.join(ADMIN_DATA_DIR, "videos")
ADMIN_FEATURES_DIR = os.path.join(ADMIN_DATA_DIR, "features")
ADMIN_QR_DIR = os.path.join(ADMIN_DATA_DIR, "qr_codes")
for d in [ADMIN_DATA_DIR, ADMIN_IMAGES_DIR, ADMIN_VIDEOS_DIR, ADMIN_FEATURES_DIR, ADMIN_QR_DIR]:
    os.makedirs(d, exist_ok=True)

# --------------------------------------------------------------------------------------------
# Bootstrap (tables + default plans + initial admin + system config)
# --------------------------------------------------------------------------------------------
with app.app_context():
    db.create_all()
    
    # Create default trial plan
    if SubscriptionPlan.query.filter_by(is_trial_plan=True).first() is None:
        trial_plan = SubscriptionPlan(
            plan_name="Free Trial",
            plan_description="Free trial with limited features",
            plan_amount=0.0,
            offer_price=0.0,
            currency="INR",
            duration_type="time",
            duration_value=7,  # 7 days trial
            trial_days=7,
            total_project_limit=1,
            total_scan_limit=50,
            is_trial_plan=True,
            features_json='["1 project only", "50 scans limit", "Trial access for 7 days"]',
            is_active=True,
            display_order=0
        )
        db.session.add(trial_plan)
    
    # Create Basic and Pro plans
    if SubscriptionPlan.query.filter_by(plan_name="Basic").first() is None:
        basic_plan = SubscriptionPlan(
            plan_name="Basic",
            plan_description="Basic subscription plan",
            plan_amount=499.0,  # ₹499
            offer_price=399.0,  # ₹399 offer
            currency="INR",
            duration_type="time",
            duration_value=6,  # 6 months
            total_project_limit=5,
            total_scan_limit=500,
            is_popular=False,
            features_json='["5 projects", "500 scans", "6 months validity", "Basic support"]',
            is_active=True,
            display_order=1
        )
        db.session.add(basic_plan)
    
    if SubscriptionPlan.query.filter_by(plan_name="Pro").first() is None:
        pro_plan = SubscriptionPlan(
            plan_name="Pro",
            plan_description="Professional subscription plan",
            plan_amount=999.0,  # ₹999
            offer_price=799.0,  # ₹799 offer
            currency="INR",
            duration_type="time",
            duration_value=12,  # 1 year
            total_project_limit=20,
            total_scan_limit=2000,
            is_popular=True,
            features_json='["20 projects", "2000 scans", "1 year validity", "Priority support", "Advanced features"]',
            is_active=True,
            display_order=2
        )
        db.session.add(pro_plan)
    
    # Create initial admin
    if Admin.query.count() == 0:
        admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@scanstory.com")
        admin_pass = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "Admin@123")
        db.session.add(Admin(
            email=admin_email.strip().lower(),
            password_hash=generate_password_hash(admin_pass),
            name="Super Admin",
            role="superadmin"
        ))
    
    # Create default system config
    if SystemConfig.query.count() == 0:
        default_configs = [
            ("free_trial_projects", "1", "integer", "Free trial project limit"),
            ("free_trial_scans", "50", "integer", "Free trial scan limit"),
            ("free_trial_days", "7", "integer", "Free trial duration in days"),
            ("razorpay_enabled", "true", "boolean", "Enable Razorpay payments"),
            ("currency", "INR", "string", "Default currency"),
        ]
        
        for key, value, config_type, description in default_configs:
            db.session.add(SystemConfig(
                config_key=key,
                config_value=value,
                config_type=config_type,
                description=description
            ))
    
    db.session.commit()

# --------------------------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------------------------

def _generate_otp() -> str:
    return f"{secrets.randbelow(1000000):06d}"

def _create_otp(email: str, purpose: str, minutes: int = 2) -> str:
    OTPCode.query.filter_by(email=email, purpose=purpose).delete()
    db.session.commit()
    code = _generate_otp()
    otp = OTPCode(
        email=email,
        code=code,
        purpose=purpose,
        expires_at=dt.utcnow() + timedelta(minutes=minutes),
    )
    db.session.add(otp)
    db.session.commit()
    return code

def _verify_otp(email: str, purpose: str, code: str) -> bool:
    rec = OTPCode.query.filter_by(email=email, purpose=purpose, code=code).first()
    if not rec:
        return False
    if dt.utcnow() > rec.expires_at:
        return False
    db.session.delete(rec)
    db.session.commit()
    return True

def get_system_config(key, default=None):
    """Get system configuration value"""
    config = SystemConfig.query.filter_by(config_key=key).first()
    if not config:
        return default
    
    if config.config_type == "integer":
        try:
            return int(config.config_value)
        except:
            return default
    elif config.config_type == "boolean":
        return config.config_value.lower() in ("true", "1", "yes", "t")
    elif config.config_type == "json":
        try:
            return json.loads(config.config_value)
        except:
            return default
    else:
        return config.config_value or default

def set_system_config(key, value, config_type="string", description=None):
    """Set system configuration value"""
    config = SystemConfig.query.filter_by(config_key=key).first()
    if config:
        config.config_value = str(value)
        config.config_type = config_type
        if description:
            config.description = description
    else:
        config = SystemConfig(
            config_key=key,
            config_value=str(value),
            config_type=config_type,
            description=description
        )
        db.session.add(config)
    db.session.commit()

def log_admin_activity(admin_id, activity_type, description):
    """Log admin activity"""
    activity = AdminActivity(
        admin_id=admin_id,
        activity_type=activity_type,
        description=description
    )
    db.session.add(activity)
    db.session.commit()    

# --------------------------------------------------------------------------------------------
# SMTP Email
# --------------------------------------------------------------------------------------------
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email_smtp(to_email: str, subject: str, html_body: str):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    mail_from = os.environ.get("MAIL_FROM", username)
    
    if not all([host, port, username, password, mail_from]):
        raise RuntimeError("SMTP env vars missing.")
    
    msg = MIMEMultipart("alternative")
    msg["From"] = mail_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    
    context = ssl.create_default_context()
    with smtplib.SMTP(host, port) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(username, password)
        server.sendmail(mail_from, to_email, msg.as_string())

def send_email_verification_otp(to_email: str, code: str, minutes: int = 2):
    html = render_template("user/email_verification.html", code=code, minutes=minutes, year=dt.utcnow().year)
    send_email_smtp(to_email, "ScanStory - Email Verification OTP", html)

def send_reset_password_otp(to_email: str, code: str, minutes: int = 2):
    now = dt.utcnow()
    html = render_template(
        "user/email_verification.html",  # ✅ CORRECT! Use email template
        code=code,
        minutes=minutes,
        now=now,
        year=now.year,
        email=to_email,
    )
    send_email_smtp(to_email, "ScanStory - Password Reset OTP", html)

def send_payment_success_email(user, plan, order):
    """Send payment success email"""
    html = render_template(
        "user/payment_success_email.html",
        user=user,
        plan=plan,
        order=order,
        year=dt.utcnow().year
    )
    send_email_smtp(user.email, "ScanStory - Payment Successful", html)

def send_admin_password_reset_email(to_email: str, code: str, minutes: int = 2):
    """Send admin password reset email"""
    html = render_template(
        "admin/reset_password_email.html",
        code=code,
        minutes=minutes,
        email=to_email,
        year=dt.utcnow().year
    )
    send_email_smtp(to_email, "ScanStory Admin - Password Reset OTP", html)

# --------------------------------------------------------------------------------------------
# Session helpers
# --------------------------------------------------------------------------------------------
def login_user(user: User):
    session["user_id"] = user.id
    session["user_email"] = user.email

def logout_user():
    session.pop("user_id", None)
    session.pop("user_email", None)

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if getattr(u, "is_blocked", False):
            logout_user()
            flash("Your account is blocked. Contact support.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def admin_login(admin: Admin):
    session["admin_id"] = admin.id
    session["admin_email"] = admin.email
    session["admin_role"] = admin.role

def admin_logout():
    session.pop("admin_id", None)
    session.pop("admin_email", None)
    session.pop("admin_role", None)

def current_admin():
    aid = session.get("admin_id")
    if not aid:
        return None
    return Admin.query.get(aid)

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_admin():
            flash("Please login as admin to access this page.", "error")
            return redirect(url_for("admin_login_route"))
        return view(*args, **kwargs)
    return wrapped

def super_admin_required(view):
    @wraps(view)
    @admin_required
    def wrapped(*args, **kwargs):
        admin = current_admin()
        if admin.role != "superadmin":
            flash("Access denied. Super admin privileges required.", "error")
            return redirect(url_for("admin_dashboard"))
        return view(*args, **kwargs)
    return wrapped

# --------------------------------------------------------------------------------------------
# Subscription Enforcement Functions
# --------------------------------------------------------------------------------------------
def check_user_limits(user):
    """
    Single source of truth enforcement:
    - Trial validity comes from TrialDetails.is_active
    - Limits come from user.subscribed_project_limit / user.subscribed_scan_limit
    """
    if user.is_blocked:
        return False, url_for("login"), "Account is blocked"

    # always ensure counters are not None
    user.projects_used = int(user.projects_used or 0)
    user.scans_used = int(user.scans_used or 0)
    user.subscribed_project_limit = int(user.subscribed_project_limit or 0)
    user.subscribed_scan_limit = int(user.subscribed_scan_limit or 0)

    # -------------------------
    # TRIAL users
    # -------------------------
    if user.subscription_status in ("trial", "limit_reached"):
        trial = TrialDetails.query.filter_by(user_id=user.id).first()

        # If trial exists, validate time
        if trial and not trial.is_active:
            user.subscription_status = "expired"
            db.session.commit()
            return False, url_for("subscribe_page"), "Trial period expired"

        # Enforce based on USER LIMITS (synced from plan)
        if user.remaining_projects <= 0:
            user.subscription_status = "limit_reached"
            db.session.commit()
            return False, url_for("subscribe_page"), f"Project limit reached ({user.subscribed_project_limit} projects)"

        if user.remaining_scans <= 0:
            user.subscription_status = "limit_reached"
            db.session.commit()
            return False, url_for("subscribe_page"), f"Scan limit reached ({user.subscribed_scan_limit} scans)"

        # If user was marked limit_reached earlier but now has quota, unlock
        if user.subscription_status == "limit_reached" and (user.remaining_projects > 0 or user.remaining_scans > 0):
            user.subscription_status = "trial"
            db.session.commit()

        return True, None, None

    # -------------------------
    # PAID users
    # -------------------------
    if user.subscription_status == "active":
        if user.subscription_expires_at and user.subscription_expires_at < dt.utcnow():
            user.subscription_status = "expired"
            db.session.commit()
            return False, url_for("subscribe_page"), "Subscription expired"

        if user.remaining_projects <= 0:
            user.subscription_status = "limit_reached"
            db.session.commit()
            return False, url_for("subscribe_page"), "Project limit reached"

        if user.remaining_scans <= 0:
            user.subscription_status = "limit_reached"
            db.session.commit()
            return False, url_for("subscribe_page"), "Scan limit reached"

        return True, None, None

    # -------------------------
    # EXPIRED / unknown
    # -------------------------
    if user.subscription_status in ("expired",):
        return False, url_for("subscribe_page"), "Please upgrade your plan"

    return True, None, None

def enforce_subscription(view):
    """Decorator to enforce subscription limits before allowing access"""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        
        # Check subscription limits
        can_proceed, redirect_url, message = check_user_limits(user)
        
        if not can_proceed and redirect_url:
            flash(message, "error")
            return redirect(redirect_url)
        
        return view(*args, **kwargs)
    return wrapped

# --------------------------------------------------------------------------------------------
# Project delete helper
# --------------------------------------------------------------------------------------------
def _delete_project_files_and_rows(project: Project):
    pairs = ProjectPair.query.filter_by(project_id=project.id).all()
    for pair in pairs:
        img_path = os.path.join(IMAGES_DIR, pair.image_filename)
        vid_path = os.path.join(VIDEOS_DIR, pair.video_filename)
        npz_path = os.path.join(FEATURES_DIR, f"{project.id}_{pair.pair_index}.npz")
        for path in (img_path, vid_path, npz_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        db.session.delete(pair)
    
    if project.qr_code_path:
        qr_file = os.path.basename(project.qr_code_path)
        qr_path = os.path.join(QR_DIR, qr_file)
        if os.path.exists(qr_path):
            try:
                os.remove(qr_path)
            except Exception:
                pass
    
    db.session.delete(project)
    db.session.commit()
    load_features.cache_clear()

# --------------------------------------------------------------------------------------------
# CV/QR functions (same as before)
# --------------------------------------------------------------------------------------------
MAX_IMAGE_SIZE = 50 * 1024 * 1024
MAX_VIDEO_SIZE = 1 * 1024 * 1024 * 1024
MAX_WORKERS = min(8, (os.cpu_count() or 4))
ORB_MAX_DIM = 1200
DETECT_MAX_DIM = 960
QUICK_TOPK = 3
QUICK_DESC_LIMIT = 140
MIN_TEST_KP = 15
MIN_GOOD_MATCHES = 10
RANSAC_REPROJ = 5.0
MIN_INLIERS_ABS = 12
MIN_INLIERS_RATIO = 0.50

_tls = threading.local()
_fast_bf = cv2.BFMatcher(cv2.NORM_HAMMING)

def _orb():
    o = getattr(_tls, "orb", None)
    if o is None:
        # ⚠️ BETTER PARAMETERS FOR MOBILE:
        o = cv2.ORB_create(
            nfeatures=1800,  # Increased features
            fastThreshold=8,  # Lower threshold = more keypoints
            scaleFactor=1.2,
            nlevels=10,  # More pyramid levels
            edgeThreshold=20,  # Smaller edge threshold for mobile
            patchSize=25  # Smaller patch size
        )
        _tls.orb = o
    return o

def _too_big(file_storage, max_bytes: int) -> bool:
    try:
        if file_storage.content_length is not None:
            return file_storage.content_length > max_bytes
        pos = file_storage.stream.tell()
        file_storage.stream.seek(0, os.SEEK_END)
        size = file_storage.stream.tell()
        file_storage.stream.seek(pos, os.SEEK_SET)
        return size > max_bytes
    except Exception:
        return False


# --------------------------------------------------------------------------------------------
# Bootstrap Function (Called from main)
# --------------------------------------------------------------------------------------------
def bootstrap_database():
    """Initialize database with default data"""
    # Create default trial plan
    if SubscriptionPlan.query.filter_by(is_trial_plan=True).first() is None:
        trial_plan = SubscriptionPlan(
            plan_name="Free Trial",
            plan_description="Free trial with limited features",
            plan_amount=0.0,
            offer_price=0.0,
            currency="INR",
            duration_type="time",
            duration_value=7,  # 7 months (as per your image)
            trial_days=7,
            total_project_limit=1,
            total_scan_limit=50,
            is_trial_plan=True,
            features_json='["1 project only", "50 scans limit", "Trial access for 7 days"]',
            is_active=True,
            display_order=0
        )
        db.session.add(trial_plan)
    
    # Create Basic and Pro plans
    if SubscriptionPlan.query.filter_by(plan_name="Basic").first() is None:
        basic_plan = SubscriptionPlan(
            plan_name="Basic",
            plan_description="Basic subscription plan",
            plan_amount=499.0,
            offer_price=399.0,
            currency="INR",
            duration_type="time",
            duration_value=6,  # 6 months
            total_project_limit=5,
            total_scan_limit=500,
            is_popular=False,
            features_json='["5 projects", "500 scans", "6 months validity", "Basic support"]',
            is_active=True,
            display_order=1
        )
        db.session.add(basic_plan)
    
    if SubscriptionPlan.query.filter_by(plan_name="Pro").first() is None:
        pro_plan = SubscriptionPlan(
            plan_name="Pro",
            plan_description="Professional subscription plan",
            plan_amount=999.0,
            offer_price=799.0,
            currency="INR",
            duration_type="time",
            duration_value=12,  # 1 year
            total_project_limit=20,
            total_scan_limit=2000,
            is_popular=True,
            features_json='["20 projects", "2000 scans", "1 year validity", "Priority support", "Advanced features"]',
            is_active=True,
            display_order=2
        )
        db.session.add(pro_plan)
    
    # Create initial super admin
    if Admin.query.count() == 0:
        admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@scanstory.com")
        admin_pass = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "Admin@123")
        super_admin = Admin(
            email=admin_email.strip().lower(),
            password_hash=generate_password_hash(admin_pass),
            name="Super Admin",
            role="superadmin",
            is_active=True
        )
        db.session.add(super_admin)
    
    # Create default system config
    if SystemConfig.query.count() == 0:
        default_configs = [
            ("free_trial_projects", "1", "integer", "Free trial project limit"),
            ("free_trial_scans", "50", "integer", "Free trial scan limit"),
            ("free_trial_days", "7", "integer", "Free trial duration in days"),
            ("razorpay_enabled", "true", "boolean", "Enable Razorpay payments"),
            ("currency", "INR", "string", "Default currency"),
            ("site_name", "ScanStory AR", "string", "Website name"),
            ("site_url", "https://scanstory.com", "string", "Website URL"),
            ("support_email", "support@scanstory.com", "string", "Support email"),
            ("max_login_attempts", "5", "integer", "Maximum login attempts"),
            ("session_timeout", "30", "integer", "Session timeout in minutes"),
        ]
        
        for key, value, config_type, description in default_configs:
            db.session.add(SystemConfig(
                config_key=key,
                config_value=value,
                config_type=config_type,
                description=description
            ))
    
    db.session.commit()
    print("✅ Database bootstrap completed successfully!")
def standardize_uploaded_image(image_path, target_size=1200):
    """Standardize image size and format before feature extraction"""
    try:
        from PIL import Image
        img = Image.open(image_path)
        
        # Convert to RGB (remove alpha channel)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize to target size (matching ORB_MAX_DIM)
        if max(img.size) > target_size:
            ratio = target_size / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            print(f"📸 Resized from {img.size} to {new_size}")
        
        # Save as JPEG
        img.save(image_path, 'JPEG', quality=92)
        return True
    except Exception as e:
        print(f"❌ Image standardization failed: {e}")
        return False
# Logo cache
_logo_cache_lock = threading.Lock()
_logo_rgba = None
_logo_path = os.path.join(LOGOS_DIR, "logo.png")

def _get_logo_rgba():
    global _logo_rgba
    with _logo_cache_lock:
        if _logo_rgba is not None:
            return _logo_rgba
        if os.path.exists(_logo_path):
            try:
                _logo_rgba = Image.open(_logo_path).convert("RGBA")
            except Exception:
                _logo_rgba = None
        return _logo_rgba

@lru_cache(maxsize=64)
def _get_logo_resized(size: int):
    logo = _get_logo_rgba()
    if logo is None:
        return None
    try:
        return logo.resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        return None

def generate_basic_qr(data, fill_color, back_color, save_path):
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=8,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color=fill_color, back_color=back_color)
        qr_img.save(save_path)
        return True
    except Exception as e:
        print(f"Basic QR generation failed: {e}")
        return False

def generate_custom_qr(data, save_path):
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=8,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        qr_image = qr.make_image(
            fill_color="black",
            back_color="white",
            image_factory=StyledPilImage,
        ).convert("RGBA")
        qr_w, qr_h = qr_image.size
        logo_size = int(min(qr_w, qr_h) * 0.22)
        logo = _get_logo_resized(logo_size)
        if logo is not None:
            pos = ((qr_w - logo_size) // 2, (qr_h - logo_size) // 2)
            plate = Image.new("RGBA", (logo_size + 18, logo_size + 18), (255, 255, 255, 235))
            qr_image.paste(plate, (pos[0] - 9, pos[1] - 9), plate)
            qr_image.paste(logo, pos, logo)
        qr_image.save(save_path)
        return True
    except Exception as e:
        print(f"QR generation failed: {e}")
        return False

def compress_video(video_path):
    try:
        info = ffmpeg.probe(video_path)
        video_streams = [s for s in info["streams"] if s.get("codec_type") == "video"]
        audio_streams = [s for s in info["streams"] if s.get("codec_type") == "audio"]
        vcodec = video_streams[0].get("codec_name") if video_streams else None
        acodec = audio_streams[0].get("codec_name") if audio_streams else None
        
        output_path = video_path.replace(".mp4", "_stored.mp4")
        if vcodec == "h264" and (acodec is None or acodec == "aac"):
            (
                ffmpeg
                .input(video_path)
                .output(output_path, **{"c:v": "copy", "c:a": "copy"}, movflags="+faststart")
                .run(overwrite_output=True, quiet=True)
            )
            return output_path
        
        output_path = video_path.replace(".mp4", "_compressed.mp4")
        (
            ffmpeg
            .input(video_path)
            .output(
                output_path,
                vcodec="libx264",
                crf=18,
                preset="veryfast",
                movflags="+faststart",
                pix_fmt="yuv420p"
            )
            .run(overwrite_output=True, quiet=True)
        )
        return output_path
    except Exception as e:
        print(f"Video processing failed: {e}")
        return video_path

# Feature extraction functions
def _kp_to_xy(kp):
    return np.array([k.pt for k in kp], dtype=np.float32) if kp else np.zeros((0, 2), dtype=np.float32)

def _make_variants(gray):
    h, w = gray.shape[:2]
    def t_id(pts): return pts
    def t_fx(pts):
        out = pts.copy(); out[:, 0] = (w - 1) - out[:, 0]; return out
    def t_fy(pts):
        out = pts.copy(); out[:, 1] = (h - 1) - out[:, 1]; return out
    def t_fxy(pts):
        out = pts.copy()
        out[:, 0] = (w - 1) - out[:, 0]
        out[:, 1] = (h - 1) - out[:, 1]
        return out
    return [
        ("n", gray, t_id),
        ("fx", cv2.flip(gray, 1), t_fx),
        ("fy", cv2.flip(gray, 0), t_fy),
        ("fxy", cv2.flip(gray, -1), t_fxy),
    ]

def extract_features_multi(image_path, save_path, max_dim=ORB_MAX_DIM):
    img = cv2.imread(image_path)
    if img is None:
        raise RuntimeError("Failed to read uploaded target image")
    gray0 = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H0, W0 = gray0.shape[:2]
    scale = 1.0
    m = max(H0, W0)
    if m > max_dim:
        scale = max_dim / float(m)
        gray = cv2.resize(gray0, (int(W0 * scale), int(H0 * scale)), interpolation=cv2.INTER_AREA)
    else:
        gray = gray0
    orb = _orb()
    payload = {}
    for tag, g, to_orig in _make_variants(gray):
        kp, desc = orb.detectAndCompute(g, None)
        if desc is None or kp is None:
            desc = np.zeros((0, 32), dtype=np.uint8)
            kp_xy = np.zeros((0, 2), dtype=np.float32)
        else:
            desc = desc.astype(np.uint8)
            kp_xy = _kp_to_xy(kp)
            kp_xy = to_orig(kp_xy)
            kp_xy = (kp_xy / scale).astype(np.float32)
        payload[f"desc_{tag}"] = desc
        payload[f"kp_{tag}"] = kp_xy
    payload["w"] = np.int32(W0)
    payload["h"] = np.int32(H0)
    np.savez(save_path, **payload)

@lru_cache(maxsize=2048)
def load_features(project_id: int, pair_index: int = 0):
    try:
        # Safely get project
        project = None
        try:
            project = Project.query.get(project_id)
        except Exception:
            # Database error - fallback to user path
            pass
        
        # Determine correct path
        if project and project.owner_admin_id:
            npz = os.path.join(ADMIN_FEATURES_DIR, f"{project_id}_{pair_index}.npz")
        else:
            npz = os.path.join(FEATURES_DIR, f"{project_id}_{pair_index}.npz")
        
        # Check if file exists
        if not os.path.exists(npz):
            return {
                "w": 0, "h": 0,
                "n": (np.zeros((0, 32), dtype=np.uint8), np.zeros((0, 2), dtype=np.float32)),
                "fx": (np.zeros((0, 32), dtype=np.uint8), np.zeros((0, 2), dtype=np.float32)),
                "fy": (np.zeros((0, 32), dtype=np.uint8), np.zeros((0, 2), dtype=np.float32)),
                "fxy": (np.zeros((0, 32), dtype=np.uint8), np.zeros((0, 2), dtype=np.float32)),
            }
        
        # Load and return features
        data = np.load(npz, allow_pickle=False)
        return {
            "w": int(data["w"]),
            "h": int(data["h"]),
            "n": (data["desc_n"].astype(np.uint8), data["kp_n"].astype(np.float32)),
            "fx": (data["desc_fx"].astype(np.uint8), data["kp_fx"].astype(np.float32)),
            "fy": (data["desc_fy"].astype(np.uint8), data["kp_fy"].astype(np.float32)),
            "fxy": (data["desc_fxy"].astype(np.uint8), data["kp_fxy"].astype(np.float32)),
        }
    except Exception:
        # Always return valid structure, never None
        return {
            "w": 0, "h": 0,
            "n": (np.zeros((0, 32), dtype=np.uint8), np.zeros((0, 2), dtype=np.float32)),
            "fx": (np.zeros((0, 32), dtype=np.uint8), np.zeros((0, 2), dtype=np.float32)),
            "fy": (np.zeros((0, 32), dtype=np.uint8), np.zeros((0, 2), dtype=np.float32)),
            "fxy": (np.zeros((0, 32), dtype=np.uint8), np.zeros((0, 2), dtype=np.float32)),
        }

def match_best_variant(test_desc, feats, ratio=0.75):
    bf = _fast_bf
    best = ("", [], None)
    for tag in ("n", "fx", "fy", "fxy"):
        stored_desc, stored_kp = feats[tag]
        if stored_desc is None or stored_desc.size == 0 or test_desc is None or test_desc.size == 0:
            continue
        try:
            knn = bf.knnMatch(test_desc, stored_desc, k=2)
        except Exception:
            continue
        good = []
        for m_n in knn:
            if len(m_n) != 2:
                continue
            m, n = m_n
            if m.distance < ratio * n.distance:
                good.append(m)
        if len(good) > len(best[1]):
            best = (tag, good, stored_kp)
    return best

def valid_corners(corners_xy, w, h):
    if corners_xy is None or len(corners_xy) != 4:
        return False
    pts = np.array(corners_xy, dtype=np.float32).reshape(4, 2)
    if not np.isfinite(pts).all():
        return False
    area = cv2.contourArea(pts)
    if area < 600:
        return False
    if area > 0.99 * (w * h):
        return False
    return True

def _resize_gray_for_detect(img_bgr, max_dim=DETECT_MAX_DIM):
    h, w = img_bgr.shape[:2]
    m = max(h, w)
    if m <= max_dim:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        return gray, 1.0, w, h
    scale = max_dim / float(m)
    new_w, new_h = int(w * scale), int(h * scale)
    small = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return gray, scale, w, h

def quick_score(test_desc, feats, ratio=0.80, max_checks=QUICK_DESC_LIMIT):
    stored_desc, _ = feats["n"]
    if stored_desc is None or stored_desc.size == 0 or test_desc is None or test_desc.size == 0:
        return 0
    td = test_desc[:max_checks] if test_desc.shape[0] > max_checks else test_desc
    try:
        knn = _fast_bf.knnMatch(td, stored_desc, k=2)
    except Exception:
        return 0
    good = 0
    for m_n in knn:
        if len(m_n) != 2:
            continue
        m, n = m_n
        # ⚠️ MORE LENIENT RATIO FOR MOBILE:
        if m.distance < 0.82 * n.distance:  # Changed from 0.80
            good += 1
    return good

def make_feature_working_jpeg(src_path: str, out_path: str, max_dim: int = ORB_MAX_DIM, jpeg_quality: int = 92) -> str:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img = cv2.imread(src_path, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Bad image for feature working jpeg")
    h, w = img.shape[:2]
    m = max(h, w)
    if m > max_dim:
        scale = max_dim / float(m)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(out_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    return out_path

def _process_pair_upload(project_id: int, i: int, image_file, video_file):
    img_filename = f"{project_id}_{i}.jpg"
    img_path = os.path.join(IMAGES_DIR, img_filename)
    image_file.save(img_path)
    
    vid_ext = os.path.splitext(video_file.filename or "")[1].lower() or ".mp4"
    vid_filename = f"{project_id}_{i}{vid_ext}"
    vid_path = os.path.join(VIDEOS_DIR, vid_filename)
    video_file.save(vid_path)
    
    # Generate .npz file for features extraction
    work_img_path = os.path.join(IMAGES_DIR, f"{project_id}_{i}_work.jpg")
    npz_path = os.path.join(FEATURES_DIR, f"{project_id}_{i}.npz")
    
    try:
        make_feature_working_jpeg(img_path, work_img_path, max_dim=ORB_MAX_DIM, jpeg_quality=92)
        extract_features_multi(work_img_path, npz_path, max_dim=ORB_MAX_DIM)
    finally:
        try:
            if os.path.exists(work_img_path):
                os.remove(work_img_path)
        except Exception:
            pass
    
    return {
        "pair_index": i,
        "image_filename": img_filename,
        "video_filename": vid_filename,
        "image_path": f"/image/{project_id}/{i}"
    }

def _process_pair_upload_simple(project_id: int, i: int, image_file, video_file):
    """Simple version without database operations"""
    # Save image
    img_filename = f"{project_id}_{i}.jpg"
    img_path = os.path.join(IMAGES_DIR, img_filename)
    image_file.save(img_path)
    
    # Save video
    vid_ext = os.path.splitext(video_file.filename or "")[1].lower() or ".mp4"
    vid_filename = f"{project_id}_{i}{vid_ext}"
    vid_path = os.path.join(VIDEOS_DIR, vid_filename)
    video_file.save(vid_path)
    
    # Generate features file (.npz)
    work_img_path = os.path.join(IMAGES_DIR, f"{project_id}_{i}_work.jpg")
    npz_path = os.path.join(FEATURES_DIR, f"{project_id}_{i}.npz")
    
    try:
        make_feature_working_jpeg(img_path, work_img_path, max_dim=ORB_MAX_DIM, jpeg_quality=92)
        extract_features_multi(work_img_path, npz_path, max_dim=ORB_MAX_DIM)
    finally:
        try:
            if os.path.exists(work_img_path):
                os.remove(work_img_path)
        except Exception:
            pass
    
    return {
        "pair_index": i,
        "image_filename": img_filename,
        "video_filename": vid_filename,
        "image_path": f"/image/{project_id}/{i}"
    }

# --------------------------------------------------------------------------------------------
# USER ROUTES
# --------------------------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def landing():
    # Fetch only active plans for landing page, ordered by display order
    plans = SubscriptionPlan.query.filter_by(is_active=True).order_by(SubscriptionPlan.display_order.asc()).all()
    return render_template("user/landing.html", plans=plans)

@app.route("/terms")
def terms_page():
    """Terms and Conditions page"""
    return render_template("user/terms.html")


@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    try:
        # Check if admin is viewing a user's dashboard
        view_user_id = request.args.get("user_id", type=int)
        admin_view = request.args.get("admin_view") == "true"
        
        if admin_view and current_admin():
            # Admin viewing user's dashboard
            user = User.query.get_or_404(view_user_id)
            # Log admin activity
            log_admin_activity(
                current_admin().id,
                "view_user_dashboard",
                f"Viewed dashboard for user: {user.email}"
            )
            print(f"👤 Admin viewing user {user.id} dashboard")
        else:
            # Regular user viewing their own dashboard
            user_id = session.get("user_id")
            if not user_id:
                return redirect(url_for("login"))
            
            user = User.query.get(user_id)
            if not user:
                print(f"DEBUG: User not found in database for id: {user_id}")
                session.pop("user_id", None)
                flash("User account not found. Please register again.", "error")
                return redirect(url_for("login"))

        if user.is_blocked:
            flash("Your account is blocked. Contact support.", "error")
            if not admin_view:
                session.pop("user_id", None)
            return redirect(url_for("login"))

        trial = None
        changed = False

        # Handle TRIAL and LIMIT_REACHED users
        if user.subscription_status in ("trial", "limit_reached"):
            try:
                trial_plan = SubscriptionPlan.query.filter_by(
                    is_trial_plan=True,
                    is_active=True
                ).first()
            except Exception as e:
                print(f"❌ Error fetching trial plan: {e}")
                trial_plan = None

            # Ensure TrialDetails exists
            try:
                trial = TrialDetails.query.filter_by(user_id=user.id).first()
            except Exception as e:
                print(f"❌ Error fetching trial details: {e}")
                trial = None
            
            if not trial:
                try:
                    now = dt.utcnow()
                    days = int(
                        (trial_plan.trial_days if trial_plan else get_system_config("free_trial_days", 7)) or 7
                    )

                    trial = TrialDetails(
                        user_id=user.id,
                        trial_start=now,
                        trial_end=now + timedelta(days=days),
                        trial_project_limit=int(get_system_config("free_trial_projects", 1) or 1),
                        trial_scan_limit=int(get_system_config("free_trial_scans", 50) or 50),
                    )
                    db.session.add(trial)
                    changed = True
                except Exception as e:
                    print(f"❌ Error creating trial details: {e}")

            # Trial expired
            if trial and not trial.is_active and user.subscription_status != "active":
                try:
                    user.subscription_status = "expired"
                    db.session.commit()
                    flash("Your free trial has expired. Please upgrade to continue.", "warning")
                    return redirect(url_for("subscribe_page"))
                except Exception as e:
                    print(f"❌ Error updating trial status: {e}")

            # Sync limits from trial plan
            if trial_plan:
                try:
                    plan_projects = int(trial_plan.total_project_limit or 1)
                    plan_scans = int(trial_plan.total_scan_limit or 50)

                    if int(user.subscribed_project_limit or 0) != plan_projects:
                        user.subscribed_project_limit = plan_projects
                        changed = True

                    if int(user.subscribed_scan_limit or 0) != plan_scans:
                        user.subscribed_scan_limit = plan_scans
                        changed = True

                    if user.subscription_id != trial_plan.id:
                        user.subscription_id = trial_plan.id
                        changed = True
                except Exception as e:
                    print(f"❌ Error syncing trial limits: {e}")

            # Ensure counters are not None
            try:
                if user.projects_used is None:
                    user.projects_used = 0
                    changed = True

                if user.scans_used is None:
                    user.scans_used = 0
                    changed = True
            except Exception as e:
                print(f"❌ Error checking user counters: {e}")

            # ✅ SAFE AUTO-UNLOCK from limit_reached
            if user.subscription_status == "limit_reached":
                try:
                    remaining_projects = int(user.subscribed_project_limit or 0) - int(user.projects_used or 0)
                    remaining_scans = int(user.subscribed_scan_limit or 0) - int(user.scans_used or 0)

                    if remaining_projects > 0 or remaining_scans > 0:
                        user.subscription_status = "trial"
                        changed = True
                except Exception as e:
                    print(f"❌ Error in auto-unlock: {e}")

        # Handle PAID users
        elif user.subscription_status == "active":
            if user.subscription_expires_at and user.subscription_expires_at < dt.utcnow():
                user.subscription_status = "expired"
                changed = True
                flash("Your subscription has expired. Please renew to continue.", "warning")

        if changed:
            try:
                db.session.commit()
                print(f"DEBUG: Changes committed for user {user.id}")
            except Exception as e:
                print(f"❌ Error committing changes: {e}")

        # Get user projects
        try:
            projects = Project.query.filter_by(
                owner_user_id=user.id
            ).order_by(Project.created_at.desc()).all()
            
            # Calculate scan count for each project
            for project in projects:
                project.scan_count = ScanLog.query.filter_by(
                    project_id=project.id,
                    is_successful=True
                ).count()
                
            print(f"DEBUG: Found {len(projects)} projects for user {user.id}")
        except Exception as e:
            print(f"❌ Error fetching projects: {e}")
            projects = []

        return render_template(
            "user/dashboard.html",
            user=user,
            projects=projects,
            trial=trial,
            admin_view=admin_view  # Pass this to template if needed
        )
        
    except Exception as e:
        print(f"❌ FATAL ERROR in dashboard route: {str(e)}")
        print(traceback.format_exc())
        return f"""
        <h1>Internal Server Error</h1>
        <p>Error: {str(e)}</p>
        <p>Please try again or contact support.</p>
        <a href="/">Go to Home</a>
        """
@app.route("/contact")
def contact_page():
    """Contact support page"""
    return render_template("user/contact.html")

@app.route('/send-contact-email', methods=['POST'])
def send_contact_email():
    try:
        # Get form data
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')
        project = request.form.get('project', 'Not specified')
        message = request.form.get('message')
        
        # Validate required fields
        if not all([name, phone, email, message]):
            return jsonify({'success': False, 'error': 'Please fill in all required fields'}), 400
        
        # Email content
        subject = f"New Contact Form Submission from {name}"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #ff007a;">New Contact Form Submission</h2>
            <hr>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 10px; background: #f5f5f5;"><strong>Name:</strong></td>
                    <td style="padding: 10px;">{name}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; background: #f5f5f5;"><strong>Phone:</strong></td>
                    <td style="padding: 10px;">{phone}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; background: #f5f5f5;"><strong>Email:</strong></td>
                    <td style="padding: 10px;">{email}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; background: #f5f5f5;"><strong>Project:</strong></td>
                    <td style="padding: 10px;">{project}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; background: #f5f5f5;"><strong>Message:</strong></td>
                    <td style="padding: 10px;">{message}</td>
                </tr>
            </table>
            <hr>
            <p style="color: #666; font-size: 12px;">This message was sent from the ScanStory contact form.</p>
        </body>
        </html>
        """
        
        # Send email using your existing SMTP function
        send_email_smtp(
            to_email="scanstory.service@gmail.com",
            subject=subject,
            html_body=html_body
        )
        
        # ✅ Return JSON success response
        return jsonify({'success': True, 'message': 'Email sent successfully'})
        
    except Exception as e:
        print(f"Contact form error: {e}")
        # ✅ Return JSON error response
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/profile")
@login_required
def user_profile():
    user = current_user()
    trial = TrialDetails.query.filter_by(user_id=user.id).first()
    projects = Project.query.filter_by(owner_user_id=user.id).order_by(Project.created_at.desc()).all()
    
    return render_template(
        "user/profile.html",
        user=user,
        trial=trial,
        projects=projects,
        get_system_config=get_system_config
    )

@app.route("/projects", methods=["GET"])
@login_required

def projects_page():
    user = current_user()
    projects = (
        Project.query
        .filter_by(owner_user_id=user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    
    # Attach pairs count
    for p in projects:
        p.pairs_count = ProjectPair.query.filter_by(project_id=p.id).count()
    
    return render_template(
        "user/projects.html",
        user=user,
        projects=projects
    )

@app.route("/projects/<int:project_id>/qr")
@login_required
def download_project_qr(project_id):
    user = current_user()
    project = Project.query.get(project_id)
    if not project or project.owner_user_id != user.id:
        abort(404)
    if not project.qr_code_filename:
        abort(404)
    return send_from_directory(
        QR_DIR,
        project.qr_code_filename,
        as_attachment=True
    )

@app.route("/projects/delete/<int:project_id>", methods=["POST"])
@login_required
def user_delete_project(project_id):
    user = current_user()
    project = Project.query.get(project_id)
    if not project or project.owner_user_id != user.id:
        abort(404)
    
    # Decrement projects count
    user.projects_used = max(0, (user.projects_used or 0) - 1)
    
    _delete_project_files_and_rows(project)
    db.session.commit()
    
    flash("Project deleted successfully.", "success")
    return redirect(url_for("projects_page"))

# --------------------------------------------------------------------------------------------
# Registration & Authentication
# --------------------------------------------------------------------------------------------
# @app.route("/register/", methods=["GET", "POST"])
# def register():
#     if request.method == "GET":
#         # Get plan_id from query parameter if provided
#         plan_id = request.args.get("plan_id", type=int)
#         selected_plan = None
#         if plan_id:
#             selected_plan = SubscriptionPlan.query.get(plan_id)
        
#         return render_template("user/register.html", selected_plan=selected_plan)

#     email = (request.form.get("email") or "").strip().lower()
#     first_name = (request.form.get("first_name") or "").strip()
#     last_name = (request.form.get("last_name") or "").strip()
#     phone = (request.form.get("phone") or "").strip()
#     password1 = request.form.get("password1") or ""
#     password2 = request.form.get("password2") or ""

#     # Validation
#     if not email:
#         flash("Email is required.", "error")
#         return render_template("user/register.html")
#     if password1 != password2:
#         flash("Passwords do not match.", "error")
#         return render_template("user/register.html")
#     if len(password1) < 6:
#         flash("Password must be at least 6 characters.", "error")
#         return render_template("user/register.html")
#     if User.query.filter_by(email=email).first():
#         flash("Email is already registered.", "error")
#         return render_template("user/register.html")

#     # Get trial plan
#     trial_plan = SubscriptionPlan.query.filter_by(is_trial_plan=True).first()
#     if not trial_plan:
#         flash("System configuration error. Please contact support.", "error")
#         return render_template("user/register.html")

#     # ✅ Safe config reads (ensure int + fallback)
#     free_trial_projects = int(get_system_config("free_trial_projects", 1) or 1)
#     free_trial_scans = int(get_system_config("free_trial_scans", 50) or 50)
#     free_trial_days = int(get_system_config("free_trial_days", 7) or 7)

#     # ✅ Use one consistent timestamp
#     now = dt.utcnow()

#     # Create user (trial activated immediately)
#     user = User(
#         email=email,
#         first_name=first_name,
#         last_name=last_name,
#         phone=phone,
#         password_hash=generate_password_hash(password1),
#         is_verified=False,
#         is_blocked=False,

#         subscription_id=trial_plan.id,
#         subscription_taken_at=now,
#         subscription_status="trial",

#         subscribed_project_limit=free_trial_projects,
#         subscribed_scan_limit=free_trial_scans,

#         projects_used=0,
#         scans_used=0
#     )

#     db.session.add(user)
#     db.session.commit()

#     # Create trial details (trial window + limits)
#     trial = TrialDetails(
#         user_id=user.id,
#         trial_start=now,
#         trial_end=now + timedelta(days=free_trial_days),
#         trial_project_limit=free_trial_projects,
#         trial_scan_limit=free_trial_scans
#     )

#     db.session.add(trial)
#     db.session.commit()

#     # Send verification OTP
#     code = _create_otp(email, "verify_email", minutes=2)
#     try:
#         send_email_verification_otp(email, code, minutes=2)
#         flash("OTP sent to your email. Please verify to continue.", "success")
#     except Exception as e:
#         flash(f"OTP created but email sending failed: {str(e)}", "error")

#     session["pending_verify_email"] = email
#     return redirect(url_for("verify_email"))

@app.route("/register/", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        plan_id = request.args.get("plan_id", type=int)
        selected_plan = None
        if plan_id:
            selected_plan = SubscriptionPlan.query.get(plan_id)
        return render_template("user/register.html", selected_plan=selected_plan)

    try:
        email = (request.form.get("email") or "").strip().lower()
        first_name = (request.form.get("first_name") or "").strip()
        last_name = (request.form.get("last_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        password1 = request.form.get("password1") or ""
        password2 = request.form.get("password2") or ""

        # Validation
        if not email:
            flash("Email is required.", "error")
            return render_template("user/register.html")
        if password1 != password2:
            flash("Passwords do not match.", "error")
            return render_template("user/register.html")
        if len(password1) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("user/register.html")
        if User.query.filter_by(email=email).first():
            flash("Email is already registered.", "error")
            return render_template("user/register.html")

        # Get trial plan
        trial_plan = SubscriptionPlan.query.filter_by(is_trial_plan=True).first()
        if not trial_plan:
            flash("System configuration error. Please contact support.", "error")
            return render_template("user/register.html")

        # Safe config reads
        free_trial_projects = int(get_system_config("free_trial_projects", 1) or 1)
        free_trial_scans = int(get_system_config("free_trial_scans", 50) or 50)
        free_trial_days = int(get_system_config("free_trial_days", 7) or 7)

        # Use one consistent timestamp
        now = dt.utcnow()

        # Create user
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            password_hash=generate_password_hash(password1),
            is_verified=False,
            is_blocked=False,
            subscription_id=trial_plan.id,
            subscription_taken_at=now,
            subscription_status="trial",
            subscribed_project_limit=free_trial_projects,
            subscribed_scan_limit=free_trial_scans,
            projects_used=0,
            scans_used=0
        )

        db.session.add(user)
        db.session.commit()

        # Create trial details
        trial = TrialDetails(
            user_id=user.id,
            trial_start=now,
            trial_end=now + timedelta(days=free_trial_days),
            trial_project_limit=free_trial_projects,
            trial_scan_limit=free_trial_scans
        )

        db.session.add(trial)
        db.session.commit()

        # Send verification OTP
        code = _create_otp(email, "verify_email", minutes=2)
        try:
            send_email_verification_otp(email, code, minutes=2)
            flash("OTP sent to your email. Please verify to continue.", "success")
        except Exception as e:
            flash(f"OTP created but email sending failed: {str(e)}", "error")

        session["pending_verify_email"] = email
        return redirect(url_for("verify_email"))

    except Exception as e:
        print(f"❌ Registration error: {str(e)}")
        print(f"❌ Error type: {type(e)}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        
        # 👇 TEMPORARILY SHOW REAL ERROR (remove after debugging)
        flash(f"Registration failed: {str(e)}", "error")
        
        return render_template("user/register.html")


@app.route("/verify-email/", methods=["GET", "POST"])
def verify_email():
    email = session.get("pending_verify_email")
    if not email:
        flash("No verification session found. Please register again.", "error")
        return redirect(url_for("register"))
    
    if request.method == "GET":
        return render_template("user/verify_email.html", email=email)
    
    otp = (request.form.get("otp") or "").strip()
    if not _verify_otp(email, "verify_email", otp):
        flash("Invalid or expired OTP. Please try again.", "error")
        return render_template("user/verify_email.html", email=email)
    
    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Account not found. Please register again.", "error")
        return redirect(url_for("register"))
    
    user.is_verified = True
    user.email_verified_at = dt.utcnow()
    db.session.commit()
    
    session.pop("pending_verify_email", None)
    flash("Email verified successfully. You can now login.", "success")
    return redirect(url_for("login"))

@app.route("/resend-otp/", methods=["GET"])
def resend_otp():
    email = session.get("pending_verify_email")
    if not email:
        flash("No verification session found.", "error")
        return redirect(url_for("register"))
    
    code = _create_otp(email, "verify_email", minutes=2)
    try:
        send_email_verification_otp(email, code, minutes=2)
        flash("A new OTP has been sent to your email.", "success")
    except Exception as e:
        flash(f"Email sending failed: {str(e)}", "error")
    
    return redirect(url_for("verify_email"))

@app.route("/login/", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("user/login.html")

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        flash("Invalid email or password.", "error")
        return render_template("user/login.html")

    if user.is_blocked:
        flash("Your account is blocked. Contact support.", "error")
        return render_template("user/login.html")

    # ✅ TRIAL SAFETY + DYNAMIC PLAN SYNC
    if user.subscription_status == "trial":
        changed = False

        # Ensure usage counters are sane
        if user.projects_used is None:
            user.projects_used = 0
            changed = True
        if user.scans_used is None:
            user.scans_used = 0
            changed = True

        # Ensure TrialDetails exists
        trial = TrialDetails.query.filter_by(user_id=user.id).first()
        if not trial:
            now = dt.utcnow()
            trial_plan = SubscriptionPlan.query.filter_by(is_trial_plan=True, is_active=True).first()
            days = int((trial_plan.trial_days if trial_plan else get_system_config("free_trial_days", 7)) or 7)

            trial = TrialDetails(
                user_id=user.id,
                trial_start=now,
                trial_end=now + timedelta(days=days),
                trial_project_limit=int(get_system_config("free_trial_projects", 1) or 1),
                trial_scan_limit=int(get_system_config("free_trial_scans", 50) or 50),
            )
            db.session.add(trial)
            changed = True

        # Trial expired → mark expired
        if not trial.is_active:
            user.subscription_status = "expired"
            db.session.commit()
            flash("Your free trial has expired. Please upgrade to continue.", "warning")
            return redirect(url_for("subscribe_page"))

        # 🔥 SYNC LIMITS FROM TRIAL PLAN (ADMIN EDIT FIX)
        trial_plan = SubscriptionPlan.query.filter_by(is_trial_plan=True, is_active=True).first()
        if trial_plan:
            plan_projects = int(trial_plan.total_project_limit or 1)
            plan_scans = int(trial_plan.total_scan_limit or 50)

            if int(user.subscribed_project_limit or 0) != plan_projects:
                user.subscribed_project_limit = plan_projects
                changed = True

            if int(user.subscribed_scan_limit or 0) != plan_scans:
                user.subscribed_scan_limit = plan_scans
                changed = True

            # Keep subscription_id consistent
            if user.subscription_id != trial_plan.id:
                user.subscription_id = trial_plan.id
                changed = True

        if changed:
            db.session.commit()

    # ✅ ADD THIS BLOCK - Login tracking update
    user.last_login_at = dt.utcnow()
    user.last_login_ip = request.remote_addr
    user.login_count = (user.login_count or 0) + 1
    db.session.commit()
    # ✅ END OF ADDED BLOCK

    # ✅ Login success
    session["user_id"] = user.id
    flash("Login successful.", "success")
    return redirect(url_for("dashboard"))



@app.route("/logout/", methods=["GET"])
def logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("landing"))

# @app.route("/forgot-password/", methods=["GET", "POST"])
# def forgot_password():
#     if request.method == "GET":
#         return render_template("user/forgot_password.html")
    
#     email = (request.form.get("email") or "").strip().lower()
#     user = User.query.filter_by(email=email).first()
    
#     if user:
#         code = _create_otp(email, "reset_password", minutes=2)
#         try:
#             send_reset_password_otp(email, code, minutes=2)
#         except Exception:
#             pass
    
#     session["pending_reset_email"] = email
#     flash("If the email exists, an OTP has been sent.", "success")
#     return redirect(url_for("reset_password"))

@app.route("/forgot-password/", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("user/forgot_password.html")
    
    email = (request.form.get("email") or "").strip().lower()
    user = User.query.filter_by(email=email).first()
    
    if user:
        try:
            code = _create_otp(email, "reset_password", minutes=2)
            send_reset_password_otp(email, code, minutes=2)
            flash("Password reset OTP has been sent to your email.", "success")
        except Exception as e:
            print(f"❌ Forgot password email error: {e}")
            flash("Could not send email. Please try again later or contact support.", "error")
            return redirect(url_for("forgot_password"))
    else:
        # For security, still show success message even if email doesn't exist
        flash("If the email exists, an OTP has been sent.", "success")
    
    session["pending_reset_email"] = email
    return redirect(url_for("reset_password"))

@app.route("/reset-password/", methods=["GET", "POST"])
def reset_password():
    email = session.get("pending_reset_email")
    if not email:
        flash("Please start from Forgot Password.", "error")
        return redirect(url_for("forgot_password"))
    
    if request.method == "GET":
        return render_template("user/reset_password.html", email=email)
    
    otp = (request.form.get("otp") or "").strip()
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""
    
    if new_password != confirm_password:
        flash("Passwords do not match.", "error")
        return render_template("user/reset_password.html", email=email)
    
    if len(new_password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return render_template("user/reset_password.html", email=email)
    
    if not _verify_otp(email, "reset_password", otp):
        flash("Invalid or expired OTP.", "error")
        return render_template("user/reset_password.html", email=email)
    
    user = User.query.filter_by(email=email).first()
    if user:
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
    
    session.pop("pending_reset_email", None)
    flash("Password updated. Please login.", "success")
    return redirect(url_for("login"))

# --------------------------------------------------------------------------------------------
# Project Creation with Subscription Enforcement
# --------------------------------------------------------------------------------------------
@app.route("/create-project", methods=["GET"])
@login_required
@enforce_subscription
def user_create_project_page():
    user = current_user()

    # enforce_subscription already checked.
    # This is just an extra safety check (optional)
    if not user.can_create_project:
        flash("Project limit reached. Please upgrade your plan.", "error")
        return redirect(url_for("subscribe_page"))

    return render_template("user/user_create_project.html", user=user)



@app.route("/upload", methods=["POST"])
@login_required
@enforce_subscription
def handle_upload():
    """Optimized project creation with background processing for MULTIPLE PAIRS"""
    user = current_user()

    if not user.can_create_project:
        flash("Project limit reached. Please upgrade your plan.", "error")
        return redirect(url_for("user_create_project_page"))

    t0 = time.time()

    # Get project name and uploaded files
    name = request.form.get("name", "Untitled Project")
    images = request.files.getlist("images")
    videos = request.files.getlist("videos")

    # Validation
    if not images or not videos or len(images) != len(videos):
        flash("Error: Please upload equal number of images and videos", "error")
        return redirect(url_for("user_create_project_page"))

    # Get max pairs based on subscription
    if user.subscription_plan and hasattr(user.subscription_plan, "max_pairs_per_project"):
        max_pairs = int(user.subscription_plan.max_pairs_per_project or 10)
    else:
        max_pairs = 10

    if len(images) > max_pairs:
        flash(f"Maximum {max_pairs} pairs allowed.", "error")
        return redirect(url_for("user_create_project_page"))

    # Quick file size check (FAST - doesn't read entire file)
    for image_file in images:
        if image_file.content_length and image_file.content_length > MAX_IMAGE_SIZE:
            flash("Image file exceeds allowed size limit.", "error")
            return redirect(url_for("user_create_project_page"))

    for video_file in videos:
        if video_file.content_length and video_file.content_length > MAX_VIDEO_SIZE:
            flash("Video file exceeds allowed size limit.", "error")
            return redirect(url_for("user_create_project_page"))

    # ✅ STEP 1: Create project record (FAST)
    project = Project(name=name, owner_user_id=user.id)
    db.session.add(project)
    db.session.commit()

    # ✅ STEP 2: Save ALL files quickly with standardization
    pairs_data = []
    for i, (image_file, video_file) in enumerate(zip(images, videos)):
        # Generate filenames
        img_filename = f"{project.id}_{i}.jpg"
        vid_ext = os.path.splitext(video_file.filename or "")[1].lower() or ".mp4"
        vid_filename = f"{project.id}_{i}{vid_ext}"
        
        # Save files (FAST)
        img_path = os.path.join(IMAGES_DIR, img_filename)
        image_file.save(img_path)
        
        # ✅ FIX: Standardize image to 1200px (match ORB_MAX_DIM)
        standardize_uploaded_image(img_path, target_size=1200)
        
        vid_path = os.path.join(VIDEOS_DIR, vid_filename)
        video_file.save(vid_path)
        
        # Create pair record (NOT processed)
        pair = ProjectPair(
            project_id=project.id,
            pair_index=i,
            image_filename=img_filename,
            video_filename=vid_filename,
            image_path=f"/image/{project.id}/{i}",
            is_processed=False  # Mark as not processed
        )
        db.session.add(pair)
        
        # Store data for background processing
        pairs_data.append({
            "pair_index": i,
            "image_filename": img_filename,
            "video_filename": vid_filename
        })

    # ✅ STEP 3: Update user count
    user.projects_used = int(user.projects_used or 0) + 1
    db.session.commit()

    # ✅ STEP 4: Generate QR code (FAST)
    user_name = (user.first_name or user.email.split("@")[0]).strip()
    scanner_url = url_for(
        "scanner",
        project_id=project.id,
        user_id=user.id,
        user_name=user_name,
        _external=True
    )
    
    qr_filename = f"project_{project.id}_main.png"
    qr_path = os.path.join(QR_DIR, qr_filename)

    ok = generate_custom_qr(scanner_url, qr_path)
    if not ok or not os.path.exists(qr_path):
        generate_basic_qr(scanner_url, "black", "white", qr_path)

    # Update project
    project.scanner_url = scanner_url
    project.qr_code_filename = qr_filename
    project.qr_code_path = f"/qr/{qr_filename}"
    db.session.commit()

    # ✅ STEP 5: Start background processing for ALL PAIRS
    try:
        import threading
        from concurrent.futures import ThreadPoolExecutor
        
        def process_single_pair_bg(project_id, pair_index, img_filename):
            """Process ONE pair in background - YOUR EXACT LOGIC"""
            try:
                # Get image path
                img_path = os.path.join(IMAGES_DIR, img_filename)
                
                # ✅ YOUR EXACT FEATURE EXTRACTION LOGIC
                work_img_path = os.path.join(IMAGES_DIR, f"{project_id}_{pair_index}_work.jpg")
                npz_path = os.path.join(FEATURES_DIR, f"{project_id}_{pair_index}.npz")
                
                # Process this single pair
                make_feature_working_jpeg(img_path, work_img_path, max_dim=ORB_MAX_DIM, jpeg_quality=92)
                extract_features_multi(work_img_path, npz_path, max_dim=ORB_MAX_DIM)
                
                # Clean up temporary file
                try:
                    if os.path.exists(work_img_path):
                        os.remove(work_img_path)
                except Exception:
                    pass
                
                # Mark as processed in database (need app context)
                with app.app_context():
                    pair = ProjectPair.query.filter_by(
                        project_id=project_id,
                        pair_index=pair_index
                    ).first()
                    if pair:
                        pair.is_processed = True
                        db.session.commit()
                        print(f"[BG] Processed pair {pair_index} for project {project_id}")
                
                return True
                
            except Exception as e:
                print(f"[BG ERROR] Failed pair {pair_index}: {e}")
                return False
        
        def background_processing_all_pairs(project_id, all_pairs_data):
            """Process ALL pairs in parallel"""
            with app.app_context():
                try:
                    print(f"[BG START] Processing {len(all_pairs_data)} pairs for project {project_id}")
                    
                    # Process pairs in parallel for speed
                    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        futures = []
                        for pair_data in all_pairs_data:
                            future = executor.submit(
                                process_single_pair_bg,
                                project_id,
                                pair_data["pair_index"],
                                pair_data["image_filename"]
                            )
                            futures.append(future)
                        
                        # Wait for all to complete
                        results = [f.result() for f in futures]
                        successful = sum(results)
                        
                        print(f"[BG DONE] Project {project_id}: {successful}/{len(all_pairs_data)} pairs processed")
                    
                    # Clear feature cache
                    load_features.cache_clear()
                    
                except Exception as e:
                    print(f"[BG FATAL ERROR] {e}")
                    import traceback
                    traceback.print_exc()
        
        # Start background processing
        thread = threading.Thread(
            target=background_processing_all_pairs,
            args=(project.id, pairs_data),
            daemon=True
        )
        thread.start()
        
        print(f"[UPLOAD] Started background processing for {len(pairs_data)} pairs")
        
    except Exception as e:
        print(f"Failed to start background processing: {e}")

    print(f"[UPLOAD COMPLETE] Project {project.id} created in {time.time() - t0:.2f}s with {len(pairs_data)} pairs")

    flash("Project created successfully! Features are processing in the background.", "success")
    return redirect(url_for("success_page", project_id=project.id))

@app.route("/project/<int:project_id>", methods=["GET"])
@login_required
def project_view(project_id):
    # Check if admin is viewing
    admin_view = request.args.get("admin_view") == "true"
    view_user_id = request.args.get("user_id", type=int)
    
    project = Project.query.get_or_404(project_id)
    
    # If admin viewing someone's project
    if admin_view and view_user_id and current_admin():
        # Admin is viewing - allow access
        user = User.query.get_or_404(view_user_id)
        print(f"👤 Admin viewing project {project_id} for user {user.id}")
    else:
        # Regular user viewing their own project
        user = current_user()
        if project.owner_user_id != user.id:
            abort(404)
    
    # Redirect to projects list or preview
    return redirect(url_for("project_preview", project_id=project_id, admin_view=admin_view, user_id=view_user_id))


# --------------------------------------------------------------------------------------------
# Subscription & Payment Routes
# --------------------------------------------------------------------------------------------
@app.route("/subscribe", methods=["GET"])
@login_required
def subscribe_page():
    """Show subscription plans"""
    user = current_user()
    plans = SubscriptionPlan.query.filter_by(is_active=True).order_by(SubscriptionPlan.display_order.asc()).all()
    
    return render_template("user/subscribe.html", 
                         plans=plans, 
                         user=user,
                         get_system_config=get_system_config)

@app.route("/create-razorpay-order", methods=["POST"])
@login_required
def create_razorpay_order():
    """Create Razorpay order for subscription"""
    user = current_user()
    plan_id = request.form.get("plan_id", type=int)
    
    if not plan_id:
        return jsonify({"success": False, "error": "Plan ID required"})
    
    plan = SubscriptionPlan.query.get(plan_id)
    if not plan or not plan.is_active or plan.is_trial_plan:
        return jsonify({"success": False, "error": "Invalid plan"})
    
    # Check if Razorpay is configured
    if not razorpay_client:
        return jsonify({
            "success": False, 
            "error": "Payment gateway not configured. Please contact support."
        })
    
    # Calculate amount in paise (Razorpay expects amount in smallest currency unit)
    try:
        amount_paise = int(plan.effective_price * 100)
        if amount_paise < 100:  # Minimum amount for Razorpay is 100 paise (₹1)
            amount_paise = 100
    except Exception as e:
        return jsonify({"success": False, "error": f"Invalid amount: {str(e)}"})
    
    # Create Razorpay order
    try:
        order_data = {
            'amount': amount_paise,
            'currency': plan.currency,
            'payment_capture': 1,  # Auto-capture payment
            'notes': {
                'user_id': str(user.id),
                'plan_id': str(plan.id),
                'plan_name': plan.plan_name,
                'user_email': user.email
            }
        }
        
        print(f"📋 Creating Razorpay order: {order_data}")
        
        razorpay_order = razorpay_client.order.create(data=order_data)
        
        # Create payment order in database
        order_id = f"ORD_{user.id}_{int(time.time())}"
        payment_order = PaymentOrder(
            order_id=order_id,
            razorpay_order_id=razorpay_order['id'],
            user_id=user.id,
            plan_id=plan.id,
            amount=plan.plan_amount,
            offer_amount=plan.offer_price,
            total_amount=plan.effective_price,
            currency=plan.currency,
            status="pending",
            purchased_project_limit=plan.total_project_limit,
            purchased_scan_limit=plan.total_scan_limit
        )
        db.session.add(payment_order)
        db.session.commit()
        
        print(f"✅ Order created: {razorpay_order['id']}")
        
        return jsonify({
            "success": True,
            "order_id": razorpay_order['id'],
            "amount": amount_paise,
            "currency": plan.currency,
            "key": RAZORPAY_KEY_ID,
            "name": "ScanStory AR Platform",
            "description": f"Subscription: {plan.plan_name}",
            "prefill": {
                "name": user.full_name or user.email.split('@')[0],
                "email": user.email,
                "contact": user.phone or "9999999999"
            },
            "theme": {
                "color": "#ff007a"
            }
        })
        
    except razorpay.errors.BadRequestError as e:
        print(f"❌ Razorpay Bad Request: {e}")
        return jsonify({"success": False, "error": f"Invalid request to payment gateway: {str(e)}"})
    except razorpay.errors.AuthenticationError as e:
        print(f"❌ Razorpay Authentication Error: {e}")
        return jsonify({"success": False, "error": "Payment gateway authentication failed. Please check API keys."})
    except Exception as e:
        print(f"❌ Razorpay order creation failed: {e}")
        return jsonify({"success": False, "error": f"Payment gateway error: {str(e)}"})

@app.route("/verify-payment", methods=["POST"])
@login_required
def verify_payment():
    """Verify Razorpay payment and activate subscription"""
    user = current_user()
    
    razorpay_payment_id = request.form.get("razorpay_payment_id")
    razorpay_order_id = request.form.get("razorpay_order_id")
    razorpay_signature = request.form.get("razorpay_signature")
    
    if not all([razorpay_payment_id, razorpay_order_id, razorpay_signature]):
        return jsonify({"success": False, "error": "Missing payment details"})
    
    # Verify signature
    params_dict = {
        'razorpay_order_id': razorpay_order_id,
        'razorpay_payment_id': razorpay_payment_id,
        'razorpay_signature': razorpay_signature
    }
    
    try:
        # Verify payment signature
        razorpay_client.utility.verify_payment_signature(params_dict)
        
        # Get payment order from database
        payment_order = PaymentOrder.query.filter_by(razorpay_order_id=razorpay_order_id).first()
        if not payment_order or payment_order.user_id != user.id:
            return jsonify({"success": False, "error": "Invalid payment order"})
        
        # Get plan details
        plan = SubscriptionPlan.query.get(payment_order.plan_id)
        if not plan:
            return jsonify({"success": False, "error": "Plan not found"})
        
        # Update payment order
        payment_order.razorpay_payment_id = razorpay_payment_id
        payment_order.razorpay_signature = razorpay_signature
        payment_order.status = "success"
        payment_order.payment_at = dt.utcnow()
        
        # Set subscription period
        payment_order.subscription_start = dt.utcnow()
        if plan.duration_type == "time":
            payment_order.subscription_end = dt.utcnow() + timedelta(days=plan.duration_value * 30)
        else:
            # For count-based plans, set far future date
            payment_order.subscription_end = dt.utcnow() + timedelta(days=365 * 10)  # 10 years
        
        # Update user subscription
        user.subscription_id = plan.id
        user.subscription_taken_at = dt.utcnow()
        user.subscription_expires_at = payment_order.subscription_end
        user.subscription_status = "active"
        user.subscribed_project_limit = plan.total_project_limit
        user.subscribed_scan_limit = plan.total_scan_limit
        user.projects_used = 0  # Reset for new subscription
        user.scans_used = 0
        
        # Update trial details if exists
        trial = TrialDetails.query.filter_by(user_id=user.id).first()
        if trial:
            trial.trial_converted = True
            trial.converted_at = dt.utcnow()
            trial.converted_plan_id = plan.id
        
        db.session.commit()
        
        # Send success email
        try:
            send_payment_success_email(user, plan, payment_order)
        except Exception as e:
            print(f"Failed to send payment success email: {e}")
        
        return jsonify({
            "success": True,
            "message": "Payment verified successfully",
            "order_id": payment_order.order_id,
            "plan_name": plan.plan_name
        })
        
    except razorpay.errors.SignatureVerificationError:
        return jsonify({"success": False, "error": "Invalid payment signature"})
    except Exception as e:
        print(f"Payment verification failed: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/payment-success")
@login_required
def payment_success():
    """Show payment success page"""
    order_id = request.args.get("order_id")
    if not order_id:
        return redirect(url_for("dashboard"))
    
    payment_order = PaymentOrder.query.filter_by(order_id=order_id, user_id=current_user().id).first()
    if not payment_order or payment_order.status != "success":
        flash("Invalid or pending payment", "error")
        return redirect(url_for("subscribe_page"))
    
    plan = SubscriptionPlan.query.get(payment_order.plan_id)
    user = current_user()
    
    return render_template("user/payment_success.html",
                         user=user,
                         plan=plan,
                         order=payment_order)

@app.route("/payment-failed")
@login_required
def payment_failed():
    """Show payment failed page"""
    flash("Payment failed. Please try again.", "error")
    return redirect(url_for("subscribe_page"))

# --------------------------------------------------------------------------------------------
# Success Page
# --------------------------------------------------------------------------------------------
@app.route("/success/<int:project_id>", methods=["GET"])
@login_required
def success_page(project_id):
    user = current_user()
    project = Project.query.get(project_id)
    
    if not project or project.owner_user_id != user.id:
        abort(404)
    
    pairs = ProjectPair.query.filter_by(project_id=project.id).order_by(ProjectPair.pair_index.asc()).all()
    
    return render_template("user/success.html", 
                         project=project, 
                         pairs=pairs, 
                         user=user)

# --------------------------------------------------------------------------------------------
# Scanner Routes (Public)
# --------------------------------------------------------------------------------------------

@app.route("/video/<int:project_id>/<int:image_id>")
def serve_video(project_id, image_id):
    project = Project.query.get(project_id)
    if not project:
        return "Project not found"
    
    pair = ProjectPair.query.filter_by(project_id=project_id, pair_index=image_id).first()
    if not pair:
        return "Pair not found"
    
    return send_from_directory(VIDEOS_DIR, pair.video_filename)

@app.route("/image/<int:project_id>/<int:image_id>")
def serve_image(project_id, image_id):
    project = Project.query.get(project_id)
    if not project:
        return "Project not found"
    
    pair = ProjectPair.query.filter_by(project_id=project_id, pair_index=image_id).first()
    if not pair:
        return "Pair not found"
    
    return send_from_directory(IMAGES_DIR, pair.image_filename)

@app.route("/qr/<filename>")
def serve_qr(filename):
    return send_from_directory(QR_DIR, filename)

@app.route("/scanner/<int:project_id>")
def scanner(project_id):
    """Public scanner - handles both user and admin projects"""
    user_id = request.args.get("user_id", type=int)
    admin_id = request.args.get("admin_id", type=int)
    user_name = request.args.get("user_name")
    admin_name = request.args.get("admin_name")
    
    # ✅ FIX: If user_id is in URL, set it in session
    # if user_id and not session.get("user_id"):
    #     session["user_id"] = user_id
    #     print(f"✅ Auto-logged in user {user_id} from QR code")

    # ✅ FIX: ALWAYS set user_id from URL into session
    if user_id:
        session["user_id"] = user_id
        session.permanent = True
        print(f"✅ FORCE set user_id {user_id} in session from QR code")
    else:
        print(f"❌ No user_id in URL - scans will not count")
    
    
    project = Project.query.get(project_id)
    
    if not project:
        return "Project not found"
    
    # Determine creator info
    if project.owner_user_id:
        creator_type = "user"
        creator_id = project.owner_user_id
        creator_name = project.owner_user.full_name if project.owner_user else "User"
    else:
        creator_type = "admin"
        creator_id = project.owner_admin_id
        creator_name = project.owner_admin.name if project.owner_admin else "Admin"
    
    return render_template(
        "user/scanner.html",
        project_id=project_id,
        project_name=project.name,
        qr_code_url=project.qr_code_path,
        user_id=user_id,
        admin_id=admin_id,
        user_name=user_name,
        admin_name=admin_name,
        creator_type=creator_type,
        creator_name=creator_name
    )
@app.route("/detect_init", methods=["POST"])
def detect_init():
    """Public detection with multi-pair support"""
    try:
        print("\n" + "="*50)
        print("🔍 DETECT_INIT CALLED")
        print("="*50)
        
        project_id = request.form.get("project_id", type=int)
        test_file = request.files.get("test_image")
        
        print(f"📌 project_id: {project_id}")
        print(f"📌 test_file: {test_file.filename if test_file else 'None'}")
        
        if not project_id or test_file is None:
            print("❌ Missing project_id or image")
            return jsonify({"detected": False, "reason": "Missing project_id or image"}), 400
        
        project = Project.query.get(project_id)
        if not project:
            print(f"❌ Project not found: {project_id}")
            return jsonify({"detected": False, "reason": "Project not found"}), 404
        
        # Get only PROCESSED pairs
        processed_pairs = ProjectPair.query.filter_by(
            project_id=project_id,
            is_processed=True
        ).order_by(ProjectPair.pair_index.asc()).all()
        
        total_pairs = ProjectPair.query.filter_by(project_id=project_id).count()
        print(f"📊 Processed pairs: {len(processed_pairs)}/{total_pairs}")
        
        if not processed_pairs:
            if total_pairs == 0:
                return jsonify({"detected": False, "reason": "No image-video pairs found"}), 400
            
            unprocessed = total_pairs - len(processed_pairs)
            return jsonify({
                "detected": False, 
                "reason": f"Project is processing ({unprocessed}/{total_pairs} pairs remaining)",
                "progress": f"0/{total_pairs}",
                "total_pairs": total_pairs,
                "ready_pairs": 0
            }), 200
        
        # Get scan session info
        user_id = session.get("user_id")
        scan_session_id = request.form.get("scan_session_id")
        
        print(f"👤 user_id: {user_id}")
        print(f"🆔 scan_session_id from request: {scan_session_id}")
        
        # If no session_id provided, generate one
        if not scan_session_id:
            scan_session_id = str(uuid.uuid4())
            print(f"⚠️ Generated new session ID: {scan_session_id}")
        else:
            print(f"✅ Using provided session ID: {scan_session_id}")
        
        scan_log = None
        user = None
        
        if user_id:
            user = User.query.get(user_id)
            print(f"👤 User found: {user is not None}")
            
            if user:
                # Check if a log already exists for this session
                existing_log = ScanLog.query.filter_by(
                    user_id=user_id,
                    scan_session_id=scan_session_id
                ).first()
                
                print(f"📝 Existing log for this session: {existing_log is not None}")
                
                if not existing_log:
                    scan_log = ScanLog(
                        project_id=project_id,
                        user_id=user_id,
                        scan_session_id=scan_session_id,
                        is_successful=False,
                        scan_type="user"
                    )
                    db.session.add(scan_log)
                    db.session.commit()
                    print(f"✅ Created NEW scan log for session {scan_session_id}")
                else:
                    scan_log = existing_log
                    print(f"✅ Using EXISTING scan log for session {scan_session_id}")
                
                if not user.can_scan:
                    print(f"❌ User cannot scan - limit reached")
                    return jsonify({
                        "detected": False, 
                        "reason": "Scan limit reached. Please upgrade your plan.",
                        "scan_session_id": scan_session_id
                    }), 403
            else:
                print(f"❌ User not found in database")
        
        # Read image
        print(f"📸 Reading image...")
        file_bytes = np.frombuffer(test_file.read(), np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        
        if img is None:
            print(f"❌ Invalid image")
            if scan_log:
                db.session.commit()
            return jsonify({"detected": False, "reason": "Invalid image"}), 400
        
        print(f"📸 Image shape: {img.shape}")
        
        # Use 1200px to match feature extraction (ORB_MAX_DIM)
        h, w = img.shape[:2]
        target_size = 1200
        if max(h, w) > target_size:
            scale = target_size / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            img = cv2.resize(img, (new_w, new_h))
            print(f"📸 Resized to: {new_w}x{new_h}")
        
        # Mobile enhancement
        h, w = img.shape[:2]
        if h < 1000 or w < 1000:
            yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV)
            yuv[:,:,0] = cv2.equalizeHist(yuv[:,:,0])
            img = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)
            kernel = np.array([[0, -0.5, 0],
                               [-0.5, 3, -0.5],
                               [0, -0.5, 0]])
            img = cv2.filter2D(img, -1, kernel)
            print(f"📸 Applied mobile enhancement")
        
        # Pass BGR image, let function handle grayscale
        gray_small, scale, orig_w, orig_h = _resize_gray_for_detect(img)
        frame_w, frame_h = orig_w, orig_h
        print(f"📸 Gray scale: {gray_small.shape}, scale: {scale}")
        
        orb = _orb()
        test_kp, test_desc = orb.detectAndCompute(gray_small, None)
        
        if test_kp is None or test_desc is None or len(test_kp) < 10:
            print(f"❌ Too few features: {len(test_kp) if test_kp else 0}")
            if scan_log:
                db.session.commit()
            return jsonify({
                "detected": False, 
                "reason": f"Too few features ({len(test_kp) if test_kp else 0})", 
                "frame_width": frame_w, 
                "frame_height": frame_h
            }), 200
        
        print(f"🔍 Found {len(test_kp)} features")
        
        # Quick scoring
        scored = []
        for pair in processed_pairs:
            feats = load_features(project_id, pair.pair_index)
            if feats is None:
                continue
            s = quick_score(test_desc, feats, ratio=0.82, max_checks=QUICK_DESC_LIMIT)
            if s > 4:
                scored.append((s, pair.pair_index))
        
        print(f"📊 Quick scoring results: {len(scored)} pairs scored >4")
        
        scored.sort(reverse=True)
        top_ids = [pid for _, pid in scored[:QUICK_TOPK]]
        if not top_ids:
            top_ids = [p.pair_index for p in processed_pairs[:min(QUICK_TOPK, len(processed_pairs))]]
        
        print(f"🎯 Top candidate pair IDs: {top_ids}")
        
        # Find best match
        best_match = None
        best_match_id = -1
        best_good = 0
        
        for pid in top_ids:
            feats = load_features(project_id, pid)
            if feats is None:
                continue
            
            best_tag, good_matches, stored_kp = match_best_variant(test_desc, feats, ratio=0.75)
            
            if not good_matches or len(good_matches) < 12:
                best_tag, good_matches, stored_kp = match_best_variant(test_desc, feats, ratio=0.80)
            
            if good_matches and len(good_matches) > best_good:
                best_good = len(good_matches)
                best_match = (best_tag, good_matches, stored_kp, feats)
                best_match_id = pid
                print(f"  - Pair {pid}: {len(good_matches)} good matches")
        
        if not best_match or best_good < 8:
            print(f"❌ Detection failed: best_good={best_good}")
            if scan_log:
                db.session.commit()
            
            return jsonify({
                "detected": False, 
                "reason": f"Mobile detection failed: Found {best_good} matches",
                "frame_width": frame_w, 
                "frame_height": frame_h
            }), 200
        
        print(f"✅ Best match: pair {best_match_id} with {best_good} matches")
        
        # Process homography
        best_tag, good_matches, stored_kp, feats = best_match
        src_pts = []
        dst_pts = []
        
        for m in good_matches:
            tp = test_kp[m.queryIdx].pt
            sp = stored_kp[m.trainIdx]
            src_pts.append([float(sp[0]), float(sp[1])])
            dst_pts.append([float(tp[0]), float(tp[1])])
        
        src_arr = np.array(src_pts, dtype=np.float32)
        dst_arr = np.array(dst_pts, dtype=np.float32)
        
        H, mask = cv2.findHomography(src_arr, dst_arr, cv2.RANSAC, RANSAC_REPROJ)
        if H is None or mask is None:
            print(f"❌ Homography failed")
            if scan_log:
                db.session.commit()
            return jsonify({"detected": False, "reason": "Homography failed"}), 200
        
        inliers = int(np.sum(mask))
        print(f"📐 Inliers: {inliers}/{len(src_arr)}")
        
        if inliers < max(8, int(0.40 * len(src_arr))):
            print(f"❌ Weak homography")
            if scan_log:
                db.session.commit()
            return jsonify({"detected": False, "reason": "Weak homography"}), 200
        
        tw, th = feats["w"], feats["h"]
        rect = np.array([[0, 0], [tw, 0], [tw, th], [0, th]], dtype=np.float32).reshape(-1, 1, 2)
        pts = cv2.perspectiveTransform(rect, H).reshape(4, 2)
        corners = [(float(p[0] / scale), float(p[1] / scale)) for p in pts]
        
        if not valid_corners(corners, frame_w, frame_h):
            print(f"❌ Bad corners")
            if scan_log:
                db.session.commit()
            return jsonify({"detected": False, "reason": "Bad corners"}), 200
        
        # ✅ Mark scan as successful
        if user and scan_log:
            scan_log.is_successful = True
            if best_match_id > 0:
                scan_log.pair_id = best_match_id
            db.session.commit()
            print(f"✅ Marked scan successful for session {scan_session_id}")
            print(f"✅ Scan log ID: {scan_log.id}, Successful: {scan_log.is_successful}")
        
        gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mask_img = np.zeros((frame_h, frame_w), dtype=np.uint8)
        cv2.fillConvexPoly(mask_img, np.array(corners, dtype=np.int32), 255)
        
        pts_track = cv2.goodFeaturesToTrack(
            gray_full,
            maxCorners=260,
            qualityLevel=0.01,
            minDistance=6,
            mask=mask_img
        )
        
        if pts_track is None:
            pts_track = np.zeros((0, 1, 2), dtype=np.float32)
        
        corners_out = [{"x": c[0], "y": c[1]} for c in corners]
        points_out = [{"x": float(p[0]), "y": float(p[1])} for p in pts_track.reshape(-1, 2)]
        
        if project.owner_admin_id:
            matched_video_url = url_for("serve_admin_video", project_id=project_id, image_id=best_match_id, _external=True)
        else:
            matched_video_url = url_for("serve_video", project_id=project_id, image_id=best_match_id, _external=True)
        
        print(f"✅ Detection successful! Returning response")
        print("="*50 + "\n")
        
        return jsonify({
            "detected": True,
            "matched_pair_id": best_match_id,
            "video_url": matched_video_url,
            "corners": corners_out,
            "init_points": points_out,
            "frame_width": frame_w,
            "frame_height": frame_h,
            "variant": best_tag,
            "inliers": inliers,
            "top_checked": top_ids,
            "scan_session_id": scan_session_id if user_id else None,
            "ready_pairs": len(processed_pairs),
            "total_pairs": total_pairs
        }), 200
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ FATAL ERROR in detect_init: {str(e)}")
        print(error_trace)
        
        return jsonify({
            "detected": False,
            "reason": "Detection service temporarily unavailable",
            "error_type": "server_error"
        }), 500
# --------------------------------------------------------------------------------------------
# --------------------------------------------------------------------------------------------
# --------------------------------------------------------------------------------------------
# @app.route("/detect_init", methods=["POST"])
# def detect_init():
#     """Public detection with multi-pair support"""
#     try:
#         project_id = request.form.get("project_id", type=int)
#         test_file = request.files.get("test_image")
        
#         if not project_id or test_file is None:
#             return jsonify({"detected": False, "reason": "Missing project_id or image"}), 400
        
#         project = Project.query.get(project_id)
#         if not project:
#             return jsonify({"detected": False, "reason": "Project not found"}), 404
        
#         # Get only PROCESSED pairs
#         processed_pairs = ProjectPair.query.filter_by(
#             project_id=project_id,
#             is_processed=True
#         ).order_by(ProjectPair.pair_index.asc()).all()
        
#         total_pairs = ProjectPair.query.filter_by(project_id=project_id).count()
        
#         if not processed_pairs:
#             if total_pairs == 0:
#                 return jsonify({"detected": False, "reason": "No image-video pairs found"}), 400
            
#             # Calculate progress
#             unprocessed = total_pairs - len(processed_pairs)
#             return jsonify({
#                 "detected": False, 
#                 "reason": f"Project is processing ({unprocessed}/{total_pairs} pairs remaining). Please try again shortly.",
#                 "progress": f"0/{total_pairs}",
#                 "total_pairs": total_pairs,
#                 "ready_pairs": 0
#             }), 200
        
#         # Get scan session info
#         user_id = session.get("user_id")
#         scan_session_id = request.form.get("scan_session_id") or str(uuid.uuid4())
#         scan_log = None
#         user = None
        
#         if user_id:
#             user = User.query.get(user_id)
#             if user:
#                 scan_log = ScanLog(
#                     project_id=project_id,
#                     user_id=user_id,
#                     scan_session_id=scan_session_id,
#                     is_successful=False,
#                     scan_type="user"
#                 )
#                 db.session.add(scan_log)
#                 db.session.commit()
                
#                 if not user.can_scan:
#                     if scan_log:
#                         db.session.delete(scan_log)
#                         db.session.commit()
#                     return jsonify({
#                         "detected": False, 
#                         "reason": "Scan limit reached. Please upgrade your plan.",
#                         "scan_session_id": scan_session_id
#                     }), 403
        
#         # Continue with detection using only processed pairs
#         file_bytes = np.frombuffer(test_file.read(), np.uint8)
#         img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        
#         if img is None:
#             if scan_log:
#                 db.session.delete(scan_log)
#                 db.session.commit()
#             return jsonify({"detected": False, "reason": "Invalid image"}), 400
        
#         # ✅ FIXED: Use 1200px to match feature extraction (ORB_MAX_DIM)
#         h, w = img.shape[:2]
#         target_size = 1200  # ← CHANGED FROM 640 TO 1200 TO MATCH FEATURE SIZE
#         if max(h, w) != target_size:
#             scale = target_size / max(h, w)
#             new_w, new_h = int(w * scale), int(h * scale)
#             img = cv2.resize(img, (new_w, new_h))
#             print(f"📸 Detection resized to: {new_w}x{new_h} for matching")
        
#         # Mobile enhancement
#         h, w = img.shape[:2]
#         if h < 1000 or w < 1000:
#             yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV)
#             yuv[:,:,0] = cv2.equalizeHist(yuv[:,:,0])
#             img = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)
#             kernel = np.array([[0, -0.5, 0],
#                                [-0.5, 3, -0.5],
#                                [0, -0.5, 0]])
#             img = cv2.filter2D(img, -1, kernel)
        
#         gray_small, scale, orig_w, orig_h = _resize_gray_for_detect(img)
#         frame_w, frame_h = orig_w, orig_h
        
#         orb = _orb()
#         test_kp, test_desc = orb.detectAndCompute(gray_small, None)
        
#         if test_kp is None or test_desc is None or len(test_kp) < 15:
#             if scan_log:
#                 db.session.delete(scan_log)
#                 db.session.commit()
#             return jsonify({
#                 "detected": False, 
#                 "reason": f"Too few features ({len(test_kp) if test_kp else 0} found)", 
#                 "frame_width": frame_w, 
#                 "frame_height": frame_h
#             }), 200
        
#         # Quick scoring with processed pairs only
#         scored = []
#         for pair in processed_pairs:
#             feats = load_features(project_id, pair.pair_index)
#             if feats is None:
#                 continue
#             s = quick_score(test_desc, feats, ratio=0.82, max_checks=QUICK_DESC_LIMIT)
#             if s > 4:
#                 scored.append((s, pair.pair_index))
        
#         scored.sort(reverse=True)
#         top_ids = [pid for _, pid in scored[:QUICK_TOPK]]
#         if not top_ids:
#             top_ids = [p.pair_index for p in processed_pairs[:min(QUICK_TOPK, len(processed_pairs))]]
        
#         # Find best match
#         best_match = None
#         best_match_id = -1
#         best_good = 0
        
#         for pid in top_ids:
#             feats = load_features(project_id, pid)
#             if feats is None:
#                 continue
            
#             best_tag, good_matches, stored_kp = match_best_variant(test_desc, feats, ratio=0.75)
            
#             if not good_matches or len(good_matches) < 12:
#                 best_tag, good_matches, stored_kp = match_best_variant(test_desc, feats, ratio=0.80)
            
#             if good_matches and len(good_matches) > best_good:
#                 best_good = len(good_matches)
#                 best_match = (best_tag, good_matches, stored_kp, feats)
#                 best_match_id = pid
        
#         if not best_match or best_good < 10:
#             if scan_log:
#                 db.session.delete(scan_log)
#                 db.session.commit()
            
#             return jsonify({
#                 "detected": False, 
#                 "reason": f"Mobile detection failed: Found {best_good} matches",
#                 "frame_width": frame_w, 
#                 "frame_height": frame_h
#             }), 200
        
#         # Process homography
#         best_tag, good_matches, stored_kp, feats = best_match
#         src_pts = []
#         dst_pts = []
        
#         for m in good_matches:
#             tp = test_kp[m.queryIdx].pt
#             sp = stored_kp[m.trainIdx]
#             src_pts.append([float(sp[0]), float(sp[1])])
#             dst_pts.append([float(tp[0]), float(tp[1])])
        
#         src_arr = np.array(src_pts, dtype=np.float32)
#         dst_arr = np.array(dst_pts, dtype=np.float32)
        
#         H, mask = cv2.findHomography(src_arr, dst_arr, cv2.RANSAC, RANSAC_REPROJ)
#         if H is None or mask is None:
#             if scan_log:
#                 db.session.delete(scan_log)
#                 db.session.commit()
#             return jsonify({"detected": False, "reason": "Homography failed", "frame_width": frame_w, "frame_height": frame_h}), 200
        
#         inliers = int(np.sum(mask))
#         if inliers < max(12, int(0.50 * len(src_arr))):
#             if scan_log:
#                 db.session.delete(scan_log)
#                 db.session.commit()
#             return jsonify({"detected": False, "reason": "Weak homography", "frame_width": frame_w, "frame_height": frame_h}), 200
        
#         tw, th = feats["w"], feats["h"]
#         rect = np.array([[0, 0], [tw, 0], [tw, th], [0, th]], dtype=np.float32).reshape(-1, 1, 2)
#         pts = cv2.perspectiveTransform(rect, H).reshape(4, 2)
#         corners = [(float(p[0] / scale), float(p[1] / scale)) for p in pts]
        
#         if not valid_corners(corners, frame_w, frame_h):
#             if scan_log:
#                 db.session.delete(scan_log)
#                 db.session.commit()
#             return jsonify({"detected": False, "reason": "Bad corners", "frame_width": frame_w, "frame_height": frame_h}), 200
        
#         # ✅ FIXED: Update scan count - ONLY ONCE PER SESSION
#         if user and scan_log:
#             scan_log.is_successful = True
#             scan_log.pair_id = best_match_id
            
#             # Check if this session already has a SUCCESSFUL scan
#             existing_scan = ScanLog.query.filter_by(
#                 user_id=user.id,
#                 scan_session_id=scan_session_id,
#                 is_successful=True
#             ).first()
            
#             if not existing_scan:
#                 user.increment_scans_used()
#                 print(f"✅ Scan counted for session: {scan_session_id}")
            
#             db.session.commit()
        
#         gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
#         mask_img = np.zeros((frame_h, frame_w), dtype=np.uint8)
#         cv2.fillConvexPoly(mask_img, np.array(corners, dtype=np.int32), 255)
        
#         pts_track = cv2.goodFeaturesToTrack(
#             gray_full,
#             maxCorners=260,
#             qualityLevel=0.01,
#             minDistance=6,
#             mask=mask_img
#         )
        
#         if pts_track is None:
#             pts_track = np.zeros((0, 1, 2), dtype=np.float32)
        
#         corners_out = [{"x": c[0], "y": c[1]} for c in corners]
#         points_out = [{"x": float(p[0]), "y": float(p[1])} for p in pts_track.reshape(-1, 2)]
        
#         if project.owner_admin_id:
#             matched_video_url = url_for("serve_admin_video", project_id=project_id, image_id=best_match_id, _external=True)
#         else:
#             matched_video_url = url_for("serve_video", project_id=project_id, image_id=best_match_id, _external=True)
        
#         return jsonify({
#             "detected": True,
#             "matched_pair_id": best_match_id,
#             "video_url": matched_video_url,
#             "corners": corners_out,
#             "init_points": points_out,
#             "frame_width": frame_w,
#             "frame_height": frame_h,
#             "variant": best_tag,
#             "inliers": inliers,
#             "top_checked": top_ids,
#             "scan_session_id": scan_session_id if user_id else None,
#             "ready_pairs": len(processed_pairs),
#             "total_pairs": total_pairs
#         }), 200
        
#     except Exception as e:
#         import traceback
#         error_trace = traceback.format_exc()
#         print(f"❌ FATAL ERROR in detect_init: {str(e)}")
#         print(error_trace)
        
#         # Log error to file for debugging
#         with open("detect_errors.log", "a") as f:
#             f.write(f"{dt.utcnow()} - Error in detect_init: {str(e)}\n")
#             f.write(f"Traceback: {error_trace}\n")
#             f.write("-" * 80 + "\n")
        
#         # ✅ CRITICAL: Always return JSON, never HTML
#         return jsonify({
#             "detected": False,
#             "reason": "Detection service temporarily unavailable. Please try again.",
#             "error_type": "server_error"
#         }), 500
# ================================================
# ✅ ADD THIS AFTER YOUR /detect_init ROUTE
# ================================================
@app.route("/api/scanner/session/end", methods=["POST"])
def scanner_session_end():
    """End scanner session - COUNT ONLY ONCE here"""
    try:
        print("\n" + "="*50)
        print("🔍 SESSION END CALLED")
        print("="*50)
        
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form
        
        print(f"📦 Received data: {data}")
        
        if not data:
            return jsonify({"ok": False, "error": "Invalid request"}), 400
        
        project_id = data.get("project_id")
        session_id = data.get("session_id")
        user_id = session.get("user_id")
        
        print(f"📌 project_id: {project_id}")
        print(f"📌 session_id: {session_id}")
        print(f"📌 user_id from session: {user_id}")
        
        if not project_id or not session_id:
            return jsonify({"ok": False, "error": "Missing required fields"}), 400
        
        # Only count for logged-in users
        if not user_id:
            print("❌ Guest user - not counting")
            return jsonify({"ok": True, "counted": False, "reason": "Guest user"})
        
        user = User.query.get(user_id)
        if not user:
            print(f"❌ User {user_id} not found")
            return jsonify({"ok": False, "error": "User not found"}), 404
        
        print(f"👤 User found: {user.email}")
        print(f"📊 Current scans_used: {user.scans_used}")
        
        # Check if this session had ANY successful scan
        successful_scan = ScanLog.query.filter_by(
            user_id=user_id,
            scan_session_id=session_id,
            is_successful=True
        ).first()
        
        print(f"✅ Successful scan found: {successful_scan is not None}")
        
        if successful_scan:
            print(f"   Log ID: {successful_scan.id}")
            print(f"   Project ID: {successful_scan.project_id}")
            print(f"   Counted: {getattr(successful_scan, 'counted', False)}")
        
        if not successful_scan:
            # Check if there are ANY logs for this session
            any_log = ScanLog.query.filter_by(
                user_id=user_id,
                scan_session_id=session_id
            ).first()
            if any_log:
                print(f"📝 Found log but is_successful={any_log.is_successful}")
            else:
                print("📝 No logs found for this session")
            
            return jsonify({"ok": True, "counted": False, "reason": "No successful detection"})
        
        # Check if already counted
        if hasattr(successful_scan, 'counted') and successful_scan.counted:
            print("⏭️ Session already counted, skipping")
            return jsonify({"ok": True, "counted": False, "reason": "Already counted"})
        
        # COUNT THE SCAN
        old_count = user.scans_used
        user.scans_used = (user.scans_used or 0) + 1
        
        # Mark as counted
        successful_scan.counted = True
        
        # Update status if limit reached
        if user.remaining_scans <= 0:
            user.subscription_status = "limit_reached"
            print("⚠️ Scan limit reached")
        
        db.session.commit()
        
        print(f"✅ COUNTED: {old_count} → {user.scans_used}")
        print("="*50 + "\n")
        
        return jsonify({
            "ok": True,
            "counted": True,
            "user_total": user.scans_used
        })
        
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
@app.route("/detect_track", methods=["POST"])
def detect_track():
    """Tracking endpoint - with scan counting"""
    try:
        project_id = request.form.get("project_id", type=int)
        pair_id = request.form.get("pair_id", type=int)
        test_file = request.files.get("test_image")
        scan_session_id = request.form.get("scan_session_id", "")
        
        if project_id is None or pair_id is None or test_file is None:
            return jsonify({"ok": False, "reason": "Missing project_id/pair_id/image"}), 400
        
        feats = load_features(project_id, pair_id)
        if feats is None:
            return jsonify({"ok": False, "reason": "Features missing"}), 404
        
        file_bytes = np.frombuffer(test_file.read(), np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        
        if img is None:
            return jsonify({"ok": False, "reason": "Invalid image"}), 400
        
        gray_small, scale, orig_w, orig_h = _resize_gray_for_detect(img)
        frame_w, frame_h = orig_w, orig_h
        
        orb = _orb()
        test_kp, test_desc = orb.detectAndCompute(gray_small, None)
        
        if test_kp is None or test_desc is None or len(test_kp) < MIN_TEST_KP:
            return jsonify({"ok": False, "reason": "Too few features", "frame_width": frame_w, "frame_height": frame_h}), 200
        
        best_tag, good_matches, stored_kp = match_best_variant(test_desc, feats, ratio=0.75)
        
        if not good_matches or len(good_matches) < MIN_GOOD_MATCHES:
            return jsonify({"ok": False, "reason": "Not enough matches", "frame_width": frame_w, "frame_height": frame_h}), 200
        
        src_pts = []
        dst_pts = []
        
        for m in good_matches:
            tp = test_kp[m.queryIdx].pt
            sp = stored_kp[m.trainIdx]
            src_pts.append([float(sp[0]), float(sp[1])])
            dst_pts.append([float(tp[0]), float(tp[1])])
        
        src_arr = np.array(src_pts, dtype=np.float32)
        dst_arr = np.array(dst_pts, dtype=np.float32)
        
        H, mask = cv2.findHomography(src_arr, dst_arr, cv2.RANSAC, RANSAC_REPROJ)
        if H is None or mask is None:
            return jsonify({"ok": False, "reason": "Homography failed", "frame_width": frame_w, "frame_height": frame_h}), 200
        
        inliers = int(np.sum(mask))
        if inliers < max(MIN_INLIERS_ABS, int(MIN_INLIERS_RATIO * len(src_arr))):
            return jsonify({"ok": False, "reason": "Weak homography", "frame_width": frame_w, "frame_height": frame_h}), 200
        
        tw, th = feats["w"], feats["h"]
        rect = np.array([[0, 0], [tw, 0], [tw, th], [0, th]], dtype=np.float32).reshape(-1, 1, 2)
        pts = cv2.perspectiveTransform(rect, H).reshape(4, 2)
        corners = [(float(p[0] / scale), float(p[1] / scale)) for p in pts]
        
        if not valid_corners(corners, frame_w, frame_h):
            return jsonify({"ok": False, "reason": "Bad corners", "frame_width": frame_w, "frame_height": frame_h}), 200
        
        corners_out = [{"x": c[0], "y": c[1]} for c in corners]
        
        return jsonify({
            "ok": True,
            "corners": corners_out,
            "frame_width": frame_w,
            "frame_height": frame_h,
            "variant": best_tag,
            "inliers": inliers
        }), 200
        
    except Exception as e:
        import traceback
        print(f"❌ ERROR in detect_track: {str(e)}")
        print(traceback.format_exc())
        
        return jsonify({
            "ok": False,
            "reason": "Tracking service temporarily unavailable"
 
       }), 500
# @app.route("/scanner/close", methods=["POST"])
# def scanner_close():
#     """Count 1 scan when user closes the AR scanner"""
#     data = request.get_json()
#     project_id = data.get("project_id")
#     user_id = session.get("user_id")
#     scan_session_id = data.get("scan_session_id")
    
#     if not user_id or not scan_session_id:
#         return jsonify({"success": False})
    
#     user = User.query.get(user_id)
#     if not user:
#         return jsonify({"success": False})
    
#     # Check if this session was already counted
#     existing_log = ScanLog.query.filter_by(
#         user_id=user_id,
#         scan_session_id=scan_session_id
#     ).first()
    
#     if existing_log:
#         return jsonify({"success": True})  # Already counted
    
#     # Create scan log and increment counter (1 scan per session)
#     scan_log = ScanLog(
#         project_id=project_id,
#         user_id=user_id,
#         scan_session_id=scan_session_id,
#         is_successful=True,
#         scan_type="user"
#     )
#     db.session.add(scan_log)
#     user.increment_scans_used()  # ← Only increments ONCE per session
#     db.session.commit()
    
#     return jsonify({"success": True})
@app.route("/project/<int:project_id>/preview")
@login_required
def project_preview(project_id):
    # Check if admin is viewing
    admin_view = request.args.get("admin_view") == "true"
    view_user_id = request.args.get("user_id", type=int)
    
    project = Project.query.get_or_404(project_id)
    
    # If admin viewing someone's project
    if admin_view and view_user_id and current_admin():
        # Admin is viewing - allow access
        user = User.query.get_or_404(view_user_id)
        print(f"👤 Admin viewing project {project_id} for user {user.id}")
    else:
        # Regular user viewing their own project
        user = current_user()
        if project.owner_user_id != user.id:
            abort(404)
    
    pairs = ProjectPair.query.filter_by(project_id=project.id).order_by(ProjectPair.pair_index).all()
    
    return render_template("user/project_preview.html",
                         user=user,
                         project=project,
                         pairs=pairs,
                         admin_view=admin_view)
# --------------------------------------------------------------------------------------------
# Admin Routes (Truncated - you can expand these)
# --------------------------------------------------------------------------------------------
@app.route("/admin_panel", methods=["GET"])
def admin_panel_redirect():
    """Redirect to admin login"""
    return redirect(url_for("admin_login_route"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login_route():
    if current_admin():
        return redirect(url_for("admin_dashboard"))
    
    if request.method == "GET":
        return render_template("admin/login.html")
    
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    admin = Admin.query.filter_by(email=email).first()
    
    if not admin or not check_password_hash(admin.password_hash, password):
        flash("Invalid email or password.", "error")
        return render_template("admin/login.html")
    
    if not admin.is_active:
        flash("Your account is deactivated. Please contact super admin.", "error")
        return render_template("admin/login.html")
    
    admin_login(admin)
    admin.last_login_at = dt.utcnow()
    admin.login_count = (admin.login_count or 0) + 1
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "login", "Admin logged in")
    
    flash("Login successful.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/forgot-password", methods=["GET", "POST"])
def admin_forgot_password():
    if request.method == "GET":
        return render_template("admin/forgot_password.html")
    
    email = (request.form.get("email") or "").strip().lower()
    admin = Admin.query.filter_by(email=email).first()
    
    if admin:
        code = _create_otp(email, "admin_reset_password", minutes=10)
        try:
            send_admin_password_reset_email(email, code, minutes=10)
        except Exception as e:
            print(f"Email sending failed: {e}")
    
    # Always show success message for security
    flash("If an admin account exists with this email, a password reset link has been sent.", "success")
    session["pending_admin_reset_email"] = email
    return redirect(url_for("admin_reset_password"))

@app.route("/admin/reset-password", methods=["GET", "POST"])
def admin_reset_password():
    email = session.get("pending_admin_reset_email")
    if not email:
        flash("Please start from Forgot Password.", "error")
        return redirect(url_for("admin_forgot_password"))
    
    if request.method == "GET":
        return render_template("admin/reset_password.html", email=email)
    
    otp = (request.form.get("otp") or "").strip()
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""
    
    if new_password != confirm_password:
        flash("Passwords do not match.", "error")
        return render_template("admin/reset_password.html", email=email)
    
    if len(new_password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return render_template("admin/reset_password.html", email=email)
    
    if not _verify_otp(email, "admin_reset_password", otp):
        flash("Invalid or expired OTP.", "error")
        return render_template("admin/reset_password.html", email=email)
    
    admin = Admin.query.filter_by(email=email).first()
    if admin:
        admin.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        # Log activity
        log_admin_activity(admin.id, "password_reset", "Admin reset password via OTP")
    
    session.pop("pending_admin_reset_email", None)
    flash("Password updated successfully. Please login.", "success")
    return redirect(url_for("admin_login_route"))

@app.route("/admin/logout")
def admin_logout_route():
    admin = current_admin()
    if admin:
        log_admin_activity(admin.id, "logout", "Admin logged out")
    admin_logout()
    flash("Logged out successfully.", "success")
    return redirect(url_for("admin_login_route"))

# --------------------------------------------------------------------------------------------
# Admin Routes - Module 2: Manage Admins (Super Admin Only)
# --------------------------------------------------------------------------------------------
@app.route("/admin/admins", methods=["GET"])
@admin_required
def admin_manage_admins():
    admin = current_admin()
    if admin.role != "superadmin":
        flash("Access denied. Super admin privileges required.", "error")
        return redirect(url_for("admin_dashboard"))
    
    admins = Admin.query.order_by(Admin.created_at.desc()).all()
    return render_template("admin/manage_admins.html", admin=admin, admins=admins)

@app.route("/admin/admins/add", methods=["GET", "POST"])
@super_admin_required
def admin_add_admin():
    admin = current_admin()
    
    if request.method == "GET":
        return render_template("admin/add_admin.html", admin=admin)
    
    # Get form data
    email = (request.form.get("email") or "").strip().lower()
    name = (request.form.get("name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    role = request.form.get("role", "admin")
    password = request.form.get("password") or ""
    
    # Validation
    if not email or not name or not password:
        flash("All fields are required.", "error")
        return render_template("admin/add_admin.html", admin=admin)
    
    if Admin.query.filter_by(email=email).first():
        flash("Admin with this email already exists.", "error")
        return render_template("admin/add_admin.html", admin=admin)
    
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return render_template("admin/add_admin.html", admin=admin)
    
    # Create admin
    new_admin = Admin(
        email=email,
        name=name,
        phone=phone,
        role=role,
        password_hash=generate_password_hash(password),
        is_active=True,
        created_by=admin.id
    )
    
    db.session.add(new_admin)
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "admin_add", f"Added new admin: {email} ({role})")
    
    flash("Admin added successfully.", "success")
    return redirect(url_for("admin_manage_admins"))

@app.route("/admin/admins/<int:admin_id>/edit", methods=["GET", "POST"])
@super_admin_required
def admin_edit_admin(admin_id):
    admin = current_admin()
    target_admin = Admin.query.get_or_404(admin_id)
    
    if request.method == "GET":
        return render_template("admin/edit_admin.html", admin=admin, target_admin=target_admin)
    
    # Get form data
    name = (request.form.get("name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    role = request.form.get("role", "admin")
    is_active = request.form.get("is_active") == "on"
    
    # Validation
    if not name:
        flash("Name is required.", "error")
        return render_template("admin/edit_admin.html", admin=admin, target_admin=target_admin)
    
    # Update admin
    target_admin.name = name
    target_admin.phone = phone
    target_admin.role = role
    target_admin.is_active = is_active
    
    # Update password if provided
    new_password = request.form.get("new_password")
    if new_password and len(new_password) >= 8:
        target_admin.password_hash = generate_password_hash(new_password)
    
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "admin_edit", f"Edited admin: {target_admin.email}")
    
    flash("Admin updated successfully.", "success")
    return redirect(url_for("admin_manage_admins"))

@app.route("/admin/admins/<int:admin_id>/delete", methods=["POST"])
@super_admin_required
def admin_delete_admin(admin_id):
    """Delete an admin account"""
    admin = current_admin()
    target_admin = Admin.query.get_or_404(admin_id)
    
    # Prevent self-deletion
    if target_admin.id == admin.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin_manage_admins"))
    
    # Prevent deleting the only super admin
    if target_admin.role == "superadmin":
        superadmin_count = Admin.query.filter_by(role="superadmin", is_active=True).count()
        if superadmin_count <= 1:
            flash("Cannot delete the only active super admin.", "error")
            return redirect(url_for("admin_manage_admins"))
    
    # Log activity before deletion
    log_admin_activity(admin.id, "admin_delete", f"Deleted admin: {target_admin.email}")
    
    db.session.delete(target_admin)
    db.session.commit()
    
    flash("Admin deleted successfully.", "success")
    return redirect(url_for("admin_manage_admins"))

@app.route("/admin/admins/<int:admin_id>/toggle-status", methods=["POST"])
@super_admin_required
def admin_toggle_admin_status(admin_id):
    admin = current_admin()
    target_admin = Admin.query.get_or_404(admin_id)
    
    # Prevent self-deactivation
    if target_admin.id == admin.id:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("admin_manage_admins"))
    
    # Prevent deactivating the only super admin
    if target_admin.role == "superadmin" and target_admin.is_active:
        superadmin_count = Admin.query.filter_by(role="superadmin", is_active=True).count()
        if superadmin_count <= 1:
            flash("Cannot deactivate the only active super admin.", "error")
            return redirect(url_for("admin_manage_admins"))
    
    # Toggle status
    target_admin.is_active = not target_admin.is_active
    db.session.commit()
    
    # Log activity
    status = "activated" if target_admin.is_active else "deactivated"
    log_admin_activity(admin.id, "admin_toggle", f"{status} admin: {target_admin.email}")
    
    flash(f"Admin {status} successfully.", "success")
    return redirect(url_for("admin_manage_admins"))

# --------------------------------------------------------------------------------------------
# Admin Routes - Module 3: Admin Dashboard
# --------------------------------------------------------------------------------------------
@app.route("/admin/dashboard", methods=["GET"])
@admin_required
def admin_dashboard():
    admin = current_admin()
    
    # ✅ GET ADMIN'S OWN PROJECTS
    admin_projects = Project.query.filter_by(
        owner_admin_id=admin.id
    ).order_by(Project.created_at.desc()).all()
    
    # Get pairs count and scan count for each project
    for p in admin_projects:
        p.pairs_count = ProjectPair.query.filter_by(project_id=p.id).count()
        p.scan_count = ScanLog.query.filter_by(project_id=p.id).count()
    
    # Get statistics
    total_users = User.query.count()
    active_users = User.query.filter_by(is_blocked=False, is_verified=True).count()
    blocked_users = User.query.filter_by(is_blocked=True).count()
    
    total_plans = SubscriptionPlan.query.count()
    active_plans = SubscriptionPlan.query.filter_by(is_active=True).count()
    
    total_projects = Project.query.count()
    total_scans = ScanLog.query.count()
    
    # Revenue statistics
    total_revenue = db.session.query(func.sum(PaymentOrder.total_amount)).filter_by(status="success").scalar() or 0
    active_subscriptions = PaymentOrder.query.filter(
        PaymentOrder.status == "success",
        PaymentOrder.subscription_end > dt.utcnow()
    ).count()
    
    # Recent payments
    recent_payments = PaymentOrder.query.filter_by(status="success").order_by(PaymentOrder.created_at.desc()).limit(10).all()
    
    # Recent users
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    
    # Plan-wise user count
    plan_stats = []
    plans = SubscriptionPlan.query.filter_by(is_active=True).all()
    for plan in plans:
        user_count = User.query.filter_by(subscription_id=plan.id).count()
        plan_stats.append({
            'plan_name': plan.plan_name,
            'user_count': user_count,
            'color': 'primary' if plan.is_popular else 'secondary'
        })
    
    return render_template("admin/dashboard.html",
                         admin=admin,
                         admin_projects=admin_projects,  # ✅ ADMIN'S PROJECTS
                         total_users=total_users,
                         active_users=active_users,
                         blocked_users=blocked_users,
                         total_plans=total_plans,
                         active_plans=active_plans,
                         total_projects=total_projects,
                         total_scans=total_scans,
                         total_revenue=total_revenue,
                         active_subscriptions=active_subscriptions,
                         recent_payments=recent_payments,
                         recent_users=recent_users,
                         plan_stats=plan_stats,
                         current_time=dt.utcnow())

# --------------------------------------------------------------------------------------------
# Admin Routes - Module 4: User Management
#
@app.route("/admin/my-projects", methods=["GET"])
@admin_required
def admin_my_projects():
    """Show ONLY the logged-in admin's own projects"""
    admin = current_admin()
    
    projects = Project.query.filter_by(
        owner_admin_id=admin.id
    ).order_by(Project.created_at.desc()).all()
    
    # Get pairs count for each project
    for p in projects:
        p.pairs_count = ProjectPair.query.filter_by(project_id=p.id).count()
        p.scan_count = ScanLog.query.filter_by(project_id=p.id).count()
    
    return render_template("admin/my_projects.html",
                         admin=admin,
                         projects=projects)
@app.route("/admin/users", methods=["GET"])
@admin_required
def admin_users():
    admin = current_admin()
    
    # Get filter parameters
    status = request.args.get("status", "all")
    plan_id = request.args.get("plan_id", type=int)
    search = request.args.get("search", "").strip()
    
    # Build query
    query = User.query
    
    if status == "active":
        query = query.filter_by(is_blocked=False, is_verified=True)
    elif status == "blocked":
        query = query.filter_by(is_blocked=True)
    elif status == "unverified":
        query = query.filter_by(is_verified=False)
    
    if plan_id:
        query = query.filter_by(subscription_id=plan_id)
    
    if search:
        query = query.filter(
            or_(
                User.email.ilike(f"%{search}%"),
                User.first_name.ilike(f"%{search}%"),
                User.last_name.ilike(f"%{search}%"),
                User.phone.ilike(f"%{search}%")
            )
        )
    
    users = query.order_by(User.created_at.desc()).all()
    plans = SubscriptionPlan.query.filter_by(is_active=True).all()
    
    return render_template("admin/users.html", 
                         admin=admin, 
                         users=users, 
                         plans=plans,
                         status=status,
                         selected_plan_id=plan_id,
                         search=search)

@app.route("/admin/users/<int:user_id>", methods=["GET"])
@admin_required
def admin_view_user(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    # Get user projects
    projects = Project.query.filter_by(owner_user_id=user.id).order_by(Project.created_at.desc()).all()
    
    # Get user payments
    payments = PaymentOrder.query.filter_by(user_id=user.id).order_by(PaymentOrder.created_at.desc()).all()
    
    # Get scan history
    scan_history = ScanLog.query.filter_by(user_id=user.id).order_by(ScanLog.created_at.desc()).limit(50).all()
    
    # Get trial details if exists
    trial = TrialDetails.query.filter_by(user_id=user.id).first()
    
    return render_template("admin/view_user.html",
                         admin=admin,
                         user=user,
                         projects=projects,
                         payments=payments,
                         scan_history=scan_history,
                         trial=trial)

@app.route("/admin/users/<int:user_id>/toggle-block", methods=["POST"])
@admin_required
def admin_toggle_block_user(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    # Toggle block status
    user.is_blocked = not user.is_blocked
    user.blocked_at = dt.utcnow() if user.is_blocked else None
    user.blocked_by = admin.id if user.is_blocked else None
    
    if user.is_blocked:
        user.blocked_reason = request.form.get("reason", "Admin action")
        action = "blocked"
    else:
        user.blocked_reason = None
        action = "unblocked"
    
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "user_block", f"{action} user: {user.email}")
    
    flash(f"User {action} successfully.", "success")
    return redirect(url_for("admin_view_user", user_id=user_id))

@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_user_password(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    new_password = request.form.get("new_password") or ""
    
    if len(new_password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect(url_for("admin_view_user", user_id=user_id))
    
    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "user_password_reset", f"Reset password for user: {user.email}")
    
    flash("User password reset successfully.", "success")
    return redirect(url_for("admin_view_user", user_id=user_id))

@app.route("/admin/users/<int:user_id>/extend-trial", methods=["POST"])
@admin_required
def admin_extend_user_trial(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    trial = TrialDetails.query.filter_by(user_id=user.id).first()
    if not trial:
        flash("User doesn't have trial details.", "error")
        return redirect(url_for("admin_view_user", user_id=user_id))
    
    extension_days = request.form.get("extension_days", type=int, default=7)
    
    trial.trial_end = trial.trial_end + timedelta(days=extension_days)
    trial.trial_extended = True
    trial.extended_days += extension_days
    trial.extended_by = admin.id
    trial.extended_at = dt.utcnow()
    trial.extended_reason = request.form.get("reason", "Admin extension")
    
    # Update user subscription status
    if user.subscription_status == "expired":
        user.subscription_status = "trial"
    
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "trial_extension", f"Extended trial for {extension_days} days for user: {user.email}")
    
    flash(f"Trial extended by {extension_days} days successfully.", "success")
    return redirect(url_for("admin_view_user", user_id=user_id))

@app.route("/admin/users/<int:user_id>/add-scans", methods=["POST"])
@admin_required
def admin_add_user_scans(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    additional_scans = request.form.get("additional_scans", type=int, default=0)
    
    if additional_scans <= 0:
        flash("Please enter a positive number of scans.", "error")
        return redirect(url_for("admin_view_user", user_id=user_id))
    
    user.subscribed_scan_limit += additional_scans
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "scan_add", f"Added {additional_scans} scans to user: {user.email}")
    
    flash(f"Added {additional_scans} scans to user's limit.", "success")
    return redirect(url_for("admin_view_user", user_id=user_id))

# --------------------------------------------------------------------------------------------
# Admin Routes - Module 5: Plan Management
# --------------------------------------------------------------------------------------------
@app.route("/admin/plans", methods=["GET"])
@admin_required
def admin_plans():
    admin = current_admin()
    plans = SubscriptionPlan.query.order_by(SubscriptionPlan.display_order.asc()).all()
    return render_template("admin/plans.html", admin=admin, plans=plans)
@app.route("/admin/project/<int:project_id>/preview")
@admin_required
def admin_project_preview(project_id):
    """Admin project preview page"""
    admin = current_admin()
    project = Project.query.get_or_404(project_id)
    
    if project.owner_admin_id != admin.id:
        abort(404)
    
    pairs = ProjectPair.query.filter_by(project_id=project.id).order_by(ProjectPair.pair_index).all()
    
    return render_template("admin/project_preview.html",
                         admin=admin,
                         project=project,
                         pairs=pairs,
                         is_admin=True)
@app.route("/admin/plans/add", methods=["GET", "POST"])
@admin_required
def admin_add_plan():
    admin = current_admin()
    
    if request.method == "GET":
        return render_template("admin/add_plan.html", admin=admin)
    
    try:
        # Get form data
        plan_name = (request.form.get("plan_name") or "").strip()
        plan_description = (request.form.get("plan_description") or "").strip()
        plan_amount = float(request.form.get("plan_amount", 0))
        offer_price = request.form.get("offer_price", "")
        offer_price = float(offer_price) if offer_price else None
        currency = request.form.get("currency", "INR")
        
        duration_type = request.form.get("duration_type", "time")
        duration_value = int(request.form.get("duration_value", 1))
        
        # Handle scan limit with "Unlimited" option
        # First check if there's a hidden input (from JavaScript)
        if 'total_scan_limit' in request.form:
            scan_limit = request.form.get("total_scan_limit", "100")
        else:
            # Fallback to the disabled input (not submitted)
            scan_limit = "100"
        
        # Convert to integer or use 999999 for unlimited
        if scan_limit.lower() == "unlimited":
            total_scan_limit = 999999
        else:
            try:
                total_scan_limit = int(scan_limit)
            except ValueError:
                total_scan_limit = 100  # Default fallback
        
        total_project_limit = int(request.form.get("total_project_limit", 1))
        
        features = request.form.get("features", "").strip()
        features_list = [f.strip() for f in features.split("\n") if f.strip()]
        
        is_popular = request.form.get("is_popular") == "on"
        is_active = request.form.get("is_active") == "on"
        display_order = int(request.form.get("display_order", 0))
        
        # Validation
        if not plan_name:
            flash("Plan name is required.", "error")
            return render_template("admin/add_plan.html", admin=admin)
        
        # Create plan
        plan = SubscriptionPlan(
            plan_name=plan_name,
            plan_description=plan_description,
            plan_amount=plan_amount,
            offer_price=offer_price,
            currency=currency,
            duration_type=duration_type,
            duration_value=duration_value,
            total_project_limit=total_project_limit,
            total_scan_limit=total_scan_limit,
            features_json=json.dumps(features_list),
            is_popular=is_popular,
            is_active=is_active,
            display_order=display_order,
            created_by=admin.id
        )
        
        db.session.add(plan)
        db.session.commit()
        
        # Create Razorpay plan if enabled
        if razorpay_client and plan_amount > 0 and get_system_config("razorpay_enabled", True):
            try:
                razorpay_plan = razorpay_client.plan.create({
                    'period': 'monthly' if duration_type == 'time' else 'yearly',
                    'interval': duration_value if duration_type == 'time' else 1,
                    'item': {
                        'name': plan_name,
                        'description': plan_description,
                        'amount': int(plan.effective_price * 100),  # Convert to paise
                        'currency': currency
                    }
                })
                plan.razorpay_plan_id = razorpay_plan['id']
                db.session.commit()
            except Exception as e:
                print(f"Razorpay plan creation failed: {e}")
        
        # Log activity
        log_admin_activity(admin.id, "plan_add", f"Added new plan: {plan_name}")
        
        flash("Plan created successfully.", "success")
        return redirect(url_for("admin_plans"))
        
    except ValueError as e:
        # Handle number conversion errors
        print(f"ValueError in plan creation: {e}")
        flash(f"Invalid number format: {str(e)}", "error")
        return render_template("admin/add_plan.html", admin=admin)
        
    except Exception as e:
        print(f"Error creating plan: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        flash(f"Error creating plan: {str(e)}", "error")
        return render_template("admin/add_plan.html", admin=admin)


@app.route("/admin/plans/<int:plan_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_plan(plan_id):
    admin = current_admin()
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    
    if request.method == "GET":
        return render_template("admin/edit_plan.html", admin=admin, plan=plan)
    
    # Get form data
    plan.plan_name = (request.form.get("plan_name") or "").strip()
    plan.plan_description = (request.form.get("plan_description") or "").strip()
    plan.plan_amount = float(request.form.get("plan_amount", 0))
    offer_price = request.form.get("offer_price", "").strip()
    plan.offer_price = float(offer_price) if offer_price else None
    
    plan.duration_type = request.form.get("duration_type", "time")
    plan.duration_value = int(request.form.get("duration_value", 1))
    
    plan.total_project_limit = int(request.form.get("total_project_limit", 1))
    plan.total_scan_limit = int(request.form.get("total_scan_limit", 100))
    
    features = request.form.get("features", "").strip()
    features_list = [f.strip() for f in features.split("\n") if f.strip()]
    plan.features_json = json.dumps(features_list)
    
    plan.is_popular = request.form.get("is_popular") == "on"
    plan.is_active = request.form.get("is_active") == "on"
    plan.display_order = int(request.form.get("display_order", 0))
    
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "plan_edit", f"Edited plan: {plan.plan_name}")
    
    flash("Plan updated successfully.", "success")
    return redirect(url_for("admin_plans"))

@app.route("/admin/plans/<int:plan_id>/delete", methods=["POST"])
@admin_required
def admin_delete_plan(plan_id):
    try:
        print(f"🔍 DELETE ROUTE CALLED for plan_id: {plan_id}")
        admin = current_admin()
        plan = SubscriptionPlan.query.get_or_404(plan_id)
        print(f"🔍 Plan found: {plan.plan_name}")
        
        # Check if plan is in use
        user_count = User.query.filter_by(subscription_id=plan.id).count()
        if user_count > 0:
            flash(f"Cannot delete plan. It is currently used by {user_count} users.", "error")
            return redirect(url_for("admin_plans"))
        
        # Log activity before deletion
        log_admin_activity(admin.id, "plan_delete", f"Deleted plan: {plan.plan_name}")
        
        db.session.delete(plan)
        db.session.commit()
        
        flash("Plan deleted successfully.", "success")
        return redirect(url_for("admin_plans"))
    except Exception as e:
        print(f"❌ Error in delete route: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error deleting plan: {str(e)}", "error")
        return redirect(url_for("admin_plans"))

@app.route("/admin/plans/<int:plan_id>/toggle-status", methods=["POST"])
@admin_required
def admin_toggle_plan_status(plan_id):
    admin = current_admin()
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    
    plan.is_active = not plan.is_active
    db.session.commit()
    
    # Log activity
    status = "activated" if plan.is_active else "deactivated"
    log_admin_activity(admin.id, "plan_toggle", f"{status} plan: {plan.plan_name}")
    
    flash(f"Plan {status} successfully.", "success")
    return redirect(url_for("admin_plans"))

# --------------------------------------------------------------------------------------------
# Admin Routes - Module 6: Subscription Management
# --------------------------------------------------------------------------------------------
@app.route("/admin/subscriptions", methods=["GET"])
@admin_required
def admin_subscriptions():
    admin = current_admin()
    
    # Get filter parameters
    status = request.args.get("status", "all")
    plan_id = request.args.get("plan_id", type=int)
    search = request.args.get("search", "").strip()
    
    # Build query
    query = PaymentOrder.query.filter_by(status="success")
    
    if status == "active":
        query = query.filter(PaymentOrder.subscription_end > dt.utcnow)
    elif status == "expired":
        query = query.filter(PaymentOrder.subscription_end <= dt.utcnow)
    
    if plan_id:
        query = query.filter_by(plan_id=plan_id)
    
    if search:
        query = query.join(User).filter(
            or_(
                User.email.ilike(f"%{search}%"),
                User.first_name.ilike(f"%{search}%"),
                User.last_name.ilike(f"%{search}%")
            )
        )
    
    subscriptions = query.order_by(PaymentOrder.created_at.desc()).all()
    plans = SubscriptionPlan.query.filter_by(is_active=True).all()
    
    # Calculate remaining projects and scans for each subscription
    for sub in subscriptions:
        user = User.query.get(sub.user_id)
        sub.user = user
        sub.remaining_projects = user.remaining_projects if user else 0
        sub.remaining_scans = user.remaining_scans if user else 0
        sub.expiry_status = "active" if sub.subscription_end and sub.subscription_end > dt.utcnow() else "expired"
    
    return render_template("admin/subscriptions.html",
                         admin=admin,
                         subscriptions=subscriptions,
                         plans=plans,
                         status=status,
                         selected_plan_id=plan_id,
                         search=search) 

@app.route("/admin/subscriptions/<int:order_id>/extend", methods=["POST"])
@admin_required
def admin_extend_subscription(order_id):
    admin = current_admin()
    payment_order = PaymentOrder.query.get_or_404(order_id)
    
    extension_months = request.form.get("extension_months", type=int, default=1)
    
    if extension_months <= 0:
        flash("Please enter a positive number of months.", "error")
        return redirect(url_for("admin_subscriptions"))
    
    # Extend subscription
    if payment_order.subscription_end:
        payment_order.subscription_end = payment_order.subscription_end + timedelta(days=30 * extension_months)
    else:
        payment_order.subscription_end = dt.utcnow() + timedelta(days=30 * extension_months)
    
    # Update user subscription
    user = User.query.get(payment_order.user_id)
    if user:
        user.subscription_expires_at = payment_order.subscription_end
        user.subscription_status = "active"
    
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "subscription_extend", 
                      f"Extended subscription by {extension_months} months for order: {payment_order.order_id}")
    
    flash(f"Subscription extended by {extension_months} months.", "success")
    return redirect(url_for("admin_subscriptions"))

@app.route("/admin/subscriptions/<int:order_id>/increase-limits", methods=["POST"])
@admin_required
def admin_increase_subscription_limits(order_id):
    admin = current_admin()
    payment_order = PaymentOrder.query.get_or_404(order_id)
    
    additional_projects = request.form.get("additional_projects", type=int, default=0)
    additional_scans = request.form.get("additional_scans", type=int, default=0)
    
    if additional_projects <= 0 and additional_scans <= 0:
        flash("Please enter positive values for projects or scans.", "error")
        return redirect(url_for("admin_subscriptions"))
    
    # Update purchase limits
    if additional_projects > 0:
        payment_order.purchased_project_limit += additional_projects
    
    if additional_scans > 0:
        payment_order.purchased_scan_limit += additional_scans
    
    # Update user limits
    user = User.query.get(payment_order.user_id)
    if user:
        if additional_projects > 0:
            user.subscribed_project_limit += additional_projects
        
        if additional_scans > 0:
            user.subscribed_scan_limit += additional_scans
        
        user.subscription_status = "active"
    
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "limits_increase",
                      f"Increased limits for order {payment_order.order_id}: +{additional_projects} projects, +{additional_scans} scans")
    
    flash("Subscription limits increased successfully.", "success")
    return redirect(url_for("admin_subscriptions"))

@app.route("/admin/subscriptions/<int:order_id>/deactivate", methods=["POST"])
@admin_required
def admin_deactivate_subscription(order_id):
    admin = current_admin()
    payment_order = PaymentOrder.query.get_or_404(order_id)
    
    # Mark subscription as expired
    payment_order.subscription_end = dt.utcnow() - timedelta(days=1)
    
    # Update user status
    user = User.query.get(payment_order.user_id)
    if user:
        user.subscription_status = "expired"
    
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "subscription_deactivate",
                      f"Deactivated subscription for order: {payment_order.order_id}")
    
    flash("Subscription deactivated successfully.", "success")
    return redirect(url_for("admin_subscriptions"))

# --------------------------------------------------------------------------------------------
# Admin Routes - Module 7: Payment Management
# --------------------------------------------------------------------------------------------
@app.route("/admin/payments", methods=["GET"])
@admin_required
def admin_payments():
    admin = current_admin()
    
    # Get filter parameters
    status = request.args.get("status", "all")
    method = request.args.get("method", "all")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    search = request.args.get("search", "").strip()
    
    # Build query
    query = PaymentOrder.query
    
    if status != "all":
        query = query.filter_by(status=status)
    
    if method != "all":
        query = query.filter_by(payment_method=method)
    
    if start_date:
        start_dt = dt.strptime(start_date, "%Y-%m-%d")
        query = query.filter(PaymentOrder.created_at >= start_dt)
    
    if end_date:
        end_dt = dt.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(PaymentOrder.created_at < end_dt)
    
    if search:
        query = query.join(User).filter(
            or_(
                PaymentOrder.order_id.ilike(f"%{search}%"),
                PaymentOrder.razorpay_order_id.ilike(f"%{search}%"),
                PaymentOrder.razorpay_payment_id.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
                User.first_name.ilike(f"%{search}%"),
                User.last_name.ilike(f"%{search}%")
            )
        )
    
    payments = query.order_by(PaymentOrder.created_at.desc()).all()
    
    # Calculate totals
    total_amount = sum(p.total_amount for p in payments)
    success_count = sum(1 for p in payments if p.status == "success")
    
    return render_template("admin/payments.html",
                         admin=admin,
                         payments=payments,
                         status=status,
                         method=method,
                         start_date=start_date,
                         end_date=end_date,
                         search=search,
                         total_amount=total_amount,
                         success_count=success_count)

@app.route("/admin/payments/<int:payment_id>", methods=["GET"])
@admin_required
def admin_view_payment(payment_id):
    admin = current_admin()
    payment = PaymentOrder.query.get_or_404(payment_id)
    
    user = User.query.get(payment.user_id)
    plan = SubscriptionPlan.query.get(payment.plan_id)
    
    return render_template("admin/view_payment.html",
                         admin=admin,
                         payment=payment,
                         user=user,
                         plan=plan)

# --------------------------------------------------------------------------------------------
# Admin Routes - Module 8: Project Monitoring
# --------------------------------------------------------------------------------------------
@app.route("/admin/projects", methods=["GET"])
@admin_required
def admin_projects():
    """Display all user profiles"""
    admin = current_admin()
    
    # Get filter parameters
    status = request.args.get("status", "all")
    plan_id = request.args.get("plan_id", type=int)
    search = request.args.get("search", "").strip()
    
    # Build query for users only
    query = User.query
    
    # Apply status filters
    if status == "active":
        query = query.filter_by(is_blocked=False)
    elif status == "blocked":
        query = query.filter_by(is_blocked=True)
    elif status == "trial":
        query = query.filter_by(subscription_status="trial")
    elif status == "paid":
        query = query.filter_by(subscription_status="active")
    
    # Apply plan filter
    if plan_id:
        query = query.filter_by(subscription_id=plan_id)
    
    # Apply search filter
    if search:
        query = query.filter(
            or_(
                User.email.ilike(f"%{search}%"),
                User.first_name.ilike(f"%{search}%"),
                User.last_name.ilike(f"%{search}%")
            )
        )
    
    users = query.order_by(User.created_at.desc()).all()
    
    # Get all plans for filter dropdown
    plans = SubscriptionPlan.query.filter_by(is_active=True).all()
    
    return render_template(
        "admin/projects.html",
        admin=admin,
        users=users,
        plans=plans,
        status=status,
        search=search,
        selected_plan_id=plan_id
    )

@app.route("/admin/projects/<int:project_id>", methods=["GET"])
@admin_required
def admin_view_project(project_id):
    admin = current_admin()
    project = Project.query.get_or_404(project_id)
    
    # Get project owner
    owner = User.query.get(project.owner_user_id) if project.owner_user_id else None
    
    # Get project pairs
    pairs = ProjectPair.query.filter_by(project_id=project_id).order_by(ProjectPair.pair_index).all()
    
    # Get scan history for this project
    scan_history = ScanLog.query.filter_by(project_id=project_id).order_by(ScanLog.created_at.desc()).limit(50).all()
    
    return render_template("admin/view_project.html",
                         admin=admin,
                         project=project,
                         owner=owner,
                         pairs=pairs,
                         scan_history=scan_history)

@app.route("/admin/projects/<int:project_id>/toggle-status", methods=["POST"])
@admin_required
def admin_toggle_project_status(project_id):
    admin = current_admin()
    project = Project.query.get_or_404(project_id)
    
    project.is_active = not project.is_active
    db.session.commit()
    
    # Log activity
    status = "activated" if project.is_active else "deactivated"
    log_admin_activity(admin.id, "project_toggle", f"{status} project: {project.name} (ID: {project.id})")
    
    flash(f"Project {status} successfully.", "success")
    return redirect(url_for("admin_view_project", project_id=project_id))

@app.route("/admin/projects/<int:project_id>/delete", methods=["POST"])
@admin_required
def admin_delete_project(project_id):
    admin = current_admin()
    project = Project.query.get_or_404(project_id)
    
    # Get user before deletion for logging
    user = User.query.get(project.owner_user_id) if project.owner_user_id else None
    
    # Delete project files and database records
    _delete_project_files_and_rows(project)
    
    # Update user project count if applicable
    if user:
        user.projects_used = max(0, (user.projects_used or 0) - 1)
        db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "project_delete", 
                      f"Deleted project: {project.name} (ID: {project.id}) owned by {user.email if user else 'unknown'}")
    
    flash("Project deleted successfully.", "success")
    return redirect(url_for("admin_projects"))

# --------------------------------------------------------------------------------------------
# Admin Routes - Module 9: Scan Usage Control
# --------------------------------------------------------------------------------------------
@app.route("/admin/scans", methods=["GET"])
@admin_required
def admin_scans():
    admin = current_admin()
    
    # Get filter parameters
    user_id = request.args.get("user_id", type=int)
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    
    # Build query
    query = db.session.query(
        User.id,
        User.email,
        User.first_name,
        User.last_name,
        func.count(ScanLog.id).label('total_scans'),
        func.sum(case((ScanLog.is_successful == True, 1), else_=0)).label('successful_scans'),
        func.max(ScanLog.created_at).label('last_scan_date')
    ).join(ScanLog, User.id == ScanLog.user_id, isouter=True)
    
    if user_id:
        query = query.filter(User.id == user_id)
    
    if start_date:
        start_dt = dt.strptime(start_date, "%Y-%m-%d")
        query = query.filter(ScanLog.created_at >= start_dt)
    
    if end_date:
        end_dt = dt.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(ScanLog.created_at < end_dt)
    
    scan_stats = query.group_by(User.id).order_by(func.count(ScanLog.id).desc()).all()
    
    # Get users for filter dropdown
    users = User.query.order_by(User.email).all()
    
    return render_template("admin/scans.html",
                         admin=admin,
                         scan_stats=scan_stats,
                         users=users,
                         selected_user_id=user_id,
                         start_date=start_date,
                         end_date=end_date)

@app.route("/admin/scans/user/<int:user_id>", methods=["GET"])
@admin_required
def admin_user_scans(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    # Get user scan history
    scan_history = ScanLog.query.filter_by(user_id=user_id).order_by(ScanLog.created_at.desc()).all()
    
    # Get scan statistics
    total_scans = len(scan_history)
    successful_scans = sum(1 for scan in scan_history if scan.is_successful)
    failed_scans = total_scans - successful_scans
    
    # Get recent scans (last 7 days)
    seven_days_ago = dt.utcnow() - timedelta(days=7)
    recent_scans = [scan for scan in scan_history if scan.created_at >= seven_days_ago]
    
    return render_template("admin/user_scans.html",
                         admin=admin,
                         user=user,
                         scan_history=scan_history,
                         total_scans=total_scans,
                         successful_scans=successful_scans,
                         failed_scans=failed_scans,
                         recent_scans=recent_scans)

@app.route("/admin/scans/<int:user_id>/update-limit", methods=["POST"])
@admin_required
def admin_update_scan_limit(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    new_scan_limit = request.form.get("new_scan_limit", type=int)
    
    if new_scan_limit is None or new_scan_limit < 0:
        flash("Please enter a valid scan limit.", "error")
        return redirect(url_for("admin_user_scans", user_id=user_id))
    
    old_limit = user.subscribed_scan_limit
    user.subscribed_scan_limit = new_scan_limit
    
    # If user was at limit and we increased it, update status
    if user.subscription_status == "limit_reached" and user.remaining_scans > 0:
        user.subscription_status = "active"
    
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "scan_limit_update",
                      f"Updated scan limit for {user.email}: {old_limit} → {new_scan_limit}")
    
    flash(f"Scan limit updated from {old_limit} to {new_scan_limit}.", "success")
    return redirect(url_for("admin_user_scans", user_id=user_id))

@app.route("/admin/scans/<int:user_id>/grant-extra", methods=["POST"])
@admin_required
def admin_grant_extra_scans(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    extra_scans = request.form.get("extra_scans", type=int, default=0)
    
    if extra_scans <= 0:
        flash("Please enter a positive number of scans.", "error")
        return redirect(url_for("admin_user_scans", user_id=user_id))
    
    user.subscribed_scan_limit += extra_scans
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "extra_scans_grant",
                      f"Granted {extra_scans} extra scans to {user.email}")
    
    flash(f"Granted {extra_scans} extra scans to user.", "success")
    return redirect(url_for("admin_user_scans", user_id=user_id))

@app.route("/admin/scans/<int:user_id>/lock-scanner", methods=["POST"])
@admin_required
def admin_lock_user_scanner(user_id):
    admin = current_admin()
    user = User.query.get_or_404(user_id)
    
    # Set scans used to limit to prevent further scans
    user.scans_used = user.subscribed_scan_limit
    user.subscription_status = "limit_reached"
    db.session.commit()
    
    # Log activity
    log_admin_activity(admin.id, "scanner_lock", f"Locked scanner for user: {user.email}")
    
    flash("Scanner locked for this user. They cannot perform more scans until limit is increased.", "success")
    return redirect(url_for("admin_user_scans", user_id=user_id))

# --------------------------------------------------------------------------------------------
# Admin Routes - System Settings
# --------------------------------------------------------------------------------------------
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    admin = current_admin()
    
    if request.method == "POST":
        # Update trial settings
        free_trial_projects = request.form.get("free_trial_projects", type=int)
        free_trial_scans = request.form.get("free_trial_scans", type=int)
        free_trial_days = request.form.get("free_trial_days", type=int)
        razorpay_enabled = request.form.get("razorpay_enabled") == "on"
        
        set_system_config("free_trial_projects", free_trial_projects, "integer", "Free trial project limit")
        set_system_config("free_trial_scans", free_trial_scans, "integer", "Free trial scan limit")
        set_system_config("free_trial_days", free_trial_days, "integer", "Free trial duration in days")
        set_system_config("razorpay_enabled", razorpay_enabled, "boolean", "Enable Razorpay payments")
        
        # Update general settings
        site_name = request.form.get("site_name", "").strip()
        site_url = request.form.get("site_url", "").strip()
        support_email = request.form.get("support_email", "").strip()
        currency = request.form.get("currency", "INR")
        
        set_system_config("site_name", site_name, "string", "Website name")
        set_system_config("site_url", site_url, "string", "Website URL")
        set_system_config("support_email", support_email, "string", "Support email")
        set_system_config("currency", currency, "string", "Default currency")
        
        # Update security settings
        max_login_attempts = request.form.get("max_login_attempts", type=int)
        session_timeout = request.form.get("session_timeout", type=int)
        
        set_system_config("max_login_attempts", max_login_attempts, "integer", "Maximum login attempts")
        set_system_config("session_timeout", session_timeout, "integer", "Session timeout in minutes")
        
        # Update other settings
        maintenance_mode = request.form.get("maintenance_mode") == "on"
        allow_registration = request.form.get("allow_registration") == "on"
        require_email_verification = request.form.get("require_email_verification") == "on"
        login_notifications = request.form.get("login_notifications") == "on"
        payment_mode = request.form.get("payment_mode", "test")
        
        set_system_config("maintenance_mode", maintenance_mode, "boolean", "Maintenance mode")
        set_system_config("allow_registration", allow_registration, "boolean", "Allow user registration")
        set_system_config("require_email_verification", require_email_verification, "boolean", "Require email verification")
        set_system_config("login_notifications", login_notifications, "boolean", "Login notifications")
        set_system_config("payment_mode", payment_mode, "string", "Payment mode")
        
        # Log activity
        log_admin_activity(admin.id, "settings_update", "Updated system settings")
        
        flash("Settings updated successfully.", "success")
        return redirect(url_for("admin_settings"))
    
    return render_template("admin/settings.html", 
                         admin=admin,
                         get_system_config=get_system_config) 

# --------------------------------------------------------------------------------------------
# Admin Routes - Activity Logs
# --------------------------------------------------------------------------------------------
@app.route("/admin/activity-logs", methods=["GET"])
@admin_required
def admin_activity_logs():
    admin = current_admin()
    
    # Get filter parameters
    activity_type = request.args.get("activity_type", "all")
    admin_id = request.args.get("admin_id", type=int)
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    
    # Build query
    query = AdminActivity.query
    
    if activity_type != "all":
        query = query.filter_by(activity_type=activity_type)
    
    if admin_id:
        query = query.filter_by(admin_id=admin_id)
    
    if start_date:
        start_dt = dt.strptime(start_date, "%Y-%m-%d")
        query = query.filter(AdminActivity.activity_at >= start_dt)
    
    if end_date:
        end_dt = dt.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(AdminActivity.activity_at < end_dt)
    
    # Get all activities (remove pagination)
    activities = query.order_by(AdminActivity.activity_at.desc()).all()
    
    # Get admins for filter dropdown
    admins = Admin.query.order_by(Admin.name).all()
    
    # Get unique activity types
    activity_types = db.session.query(AdminActivity.activity_type).distinct().all()
    activity_types = [at[0] for at in activity_types]
    
    return render_template("admin/activity_logs.html",
                         admin=admin,
                         activities=activities,  # Just pass all activities
                         admins=admins,
                         activity_types=activity_types,
                         selected_activity_type=activity_type,
                         selected_admin_id=admin_id,
                         start_date=start_date,
                         end_date=end_date)

# --------------------------------------------------------------------------------------------
# Admin Routes - Project Creation (Unlimited & Free for Admin)
# --------------------------------------------------------------------------------------------
@app.route("/admin/projects/create", methods=["GET"])
@admin_required
def admin_create_project_page():
    """GET: Show the project creation form for admin"""
    admin = current_admin()
    return render_template("user/user_create_project.html", 
                         user=admin, 
                         is_admin=True,
                         get_system_config=get_system_config)
@app.route("/admin/projects/upload", methods=["POST"])
@admin_required
def admin_handle_upload():
    """Admin project creation with fast multi-pair processing"""
    admin = current_admin()
    
    t0 = time.time()
    
    # Get project name and uploaded files
    name = request.form.get("name", "Untitled Project")
    images = request.files.getlist("images")
    videos = request.files.getlist("videos")

    # Validation
    if not images or not videos or len(images) != len(videos):
        flash("Error: Please upload equal number of images and videos", "error")
        return redirect(url_for("admin_create_project_page"))
    
    # Admin can upload unlimited pairs
    max_pairs = 50
    
    if len(images) > max_pairs:
        flash(f"Maximum {max_pairs} pairs allowed.", "error")
        return redirect(url_for("admin_create_project_page"))
    
    # Quick file size check
    for image_file in images:
        if image_file.content_length and image_file.content_length > MAX_IMAGE_SIZE:
            flash("Image file exceeds allowed size limit.", "error")
            return redirect(url_for("admin_create_project_page"))
    
    for video_file in videos:
        if video_file.content_length and video_file.content_length > MAX_VIDEO_SIZE:
            flash("Video file exceeds allowed size limit.", "error")
            return redirect(url_for("admin_create_project_page"))
    
    # Create project
    project = Project(
        name=name, 
        owner_admin_id=admin.id,
        owner_user_id=None
    )
    db.session.add(project)
    db.session.commit()
    
    # Save ALL files quickly
    pairs_data = []
    for i, (image_file, video_file) in enumerate(zip(images, videos)):
        # Generate filenames
        img_filename = f"{project.id}_{i}.jpg"
        vid_ext = os.path.splitext(video_file.filename or "")[1].lower() or ".mp4"
        vid_filename = f"{project.id}_{i}{vid_ext}"
        
        # ✅ CHANGE 1: Save to ADMIN folders
        img_path = os.path.join(ADMIN_IMAGES_DIR, img_filename)  # ← CHANGED
        image_file.save(img_path)
        
        vid_path = os.path.join(ADMIN_VIDEOS_DIR, vid_filename)  # ← CHANGED
        video_file.save(vid_path)
        
        # ✅ CHANGE 2: Use admin image URL
        pair = ProjectPair(
            project_id=project.id,
            pair_index=i,
            image_filename=img_filename,
            video_filename=vid_filename,
            image_path=f"/admin/image/{project.id}/{i}",  # ← CHANGED
            is_processed=False
        )
        db.session.add(pair)
        
        pairs_data.append({
            "pair_index": i,
            "image_filename": img_filename,
            "video_filename": vid_filename
        })
    
    db.session.commit()
    
    # Generate QR code
    admin_name = admin.name or admin.email.split("@")[0]
    
    scanner_url = url_for(
        "scanner",
        project_id=project.id,
        admin_id=admin.id,
        admin_name=admin_name,
        _external=True
    )
    
    qr_filename = f"project_{project.id}_admin.png"
    # ✅ CHANGE 3: Save QR to ADMIN folder
    qr_path = os.path.join(ADMIN_QR_DIR, qr_filename)  # ← CHANGED
    
    ok = generate_custom_qr(scanner_url, qr_path)
    if not ok or not os.path.exists(qr_path):
        generate_basic_qr(scanner_url, "black", "white", qr_path)
    
    # Update project
    project.scanner_url = scanner_url
    project.qr_code_filename = qr_filename
    # ✅ CHANGE 4: Use admin QR URL
    project.qr_code_path = f"/admin/qr/{qr_filename}"  # ← CHANGED
    db.session.commit()
    
    # Start background processing for admin project
    try:
        import threading
        from concurrent.futures import ThreadPoolExecutor
        
        def process_single_pair_bg_admin(project_id, pair_index, img_filename):
            """Process ONE admin pair in background"""
            try:
                # ✅ CHANGE 5: Use ADMIN paths in background
                img_path = os.path.join(ADMIN_IMAGES_DIR, img_filename)  # ← CHANGED
                work_img_path = os.path.join(ADMIN_IMAGES_DIR, f"{project_id}_{pair_index}_work.jpg")  # ← CHANGED
                npz_path = os.path.join(ADMIN_FEATURES_DIR, f"{project_id}_{pair_index}.npz")  # ← CHANGED
                
                make_feature_working_jpeg(img_path, work_img_path, max_dim=ORB_MAX_DIM, jpeg_quality=92)
                extract_features_multi(work_img_path, npz_path, max_dim=ORB_MAX_DIM)
                
                # Clean up
                try:
                    if os.path.exists(work_img_path):
                        os.remove(work_img_path)
                except Exception:
                    pass
                
                # Update database
                with app.app_context():
                    pair = ProjectPair.query.filter_by(
                        project_id=project_id,
                        pair_index=pair_index
                    ).first()
                    if pair:
                        pair.is_processed = True
                        db.session.commit()
                
                return True
                
            except Exception as e:
                print(f"[ADMIN BG ERROR] Failed pair {pair_index}: {e}")
                return False
        
        def background_processing_admin(project_id, all_pairs_data):
            """Process all admin pairs in parallel"""
            with app.app_context():
                try:
                    print(f"[ADMIN BG] Processing {len(all_pairs_data)} pairs")
                    
                    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        futures = []
                        for pair_data in all_pairs_data:
                            future = executor.submit(
                                process_single_pair_bg_admin,
                                project_id,
                                pair_data["pair_index"],
                                pair_data["image_filename"]
                            )
                            futures.append(future)
                        
                        results = [f.result() for f in futures]
                        successful = sum(results)
                        
                        print(f"[ADMIN BG DONE] {successful}/{len(all_pairs_data)} pairs processed")
                    
                    load_features.cache_clear()
                    
                except Exception as e:
                    print(f"[ADMIN BG FATAL ERROR] {e}")
        
        # Start thread
        thread = threading.Thread(
            target=background_processing_admin,
            args=(project.id, pairs_data),
            daemon=True
        )
        thread.start()
        
    except Exception as e:
        print(f"Admin background thread failed: {e}")
    
    print(f"[ADMIN UPLOAD] Project {project.id} created in {time.time() - t0:.2f}s with {len(pairs_data)} pairs")
    
    flash("Project created successfully!", "success")
    return redirect(url_for("admin_success_page", project_id=project.id))
# ============================================================
# ADMIN FILE SERVING ROUTES
# ============================================================
@app.route("/admin/image/<int:project_id>/<int:image_id>")
def serve_admin_image(project_id, image_id):
    """Serve images for ADMIN projects only"""
    project = Project.query.get(project_id)
    if not project or not project.owner_admin_id:
        abort(404)
    
    pair = ProjectPair.query.filter_by(project_id=project_id, pair_index=image_id).first()
    if not pair:
        abort(404)
    
    # ✅ ADD THIS CHECK
    file_path = os.path.join(ADMIN_IMAGES_DIR, pair.image_filename)
    if not os.path.exists(file_path):
        print(f"❌ Admin image not found: {file_path}")
        abort(404)
    
    return send_from_directory(ADMIN_IMAGES_DIR, pair.image_filename)
@app.route("/admin/video/<int:project_id>/<int:image_id>")
def serve_admin_video(project_id, image_id):
    """Serve videos for ADMIN projects only"""
    project = Project.query.get(project_id)
    if not project or not project.owner_admin_id:
        abort(404)
    
    pair = ProjectPair.query.filter_by(project_id=project_id, pair_index=image_id).first()
    if not pair:
        abort(404)
    
    return send_from_directory(ADMIN_VIDEOS_DIR, pair.video_filename)

@app.route("/admin/qr/<filename>")
def serve_admin_qr(filename):
    """Serve QR codes for ADMIN projects only"""
    # Extract project ID from filename (format: project_123_admin.png)
    try:
        project_id = int(filename.split('_')[1])
    except:
        abort(404)
    
    project = Project.query.get(project_id)
    if not project or not project.owner_admin_id:
        abort(404)
    
    return send_from_directory(ADMIN_QR_DIR, filename)

@app.route("/admin/success/<int:project_id>", methods=["GET"])
@admin_required
def admin_success_page(project_id):
    """Success page for admin project creation"""
    admin = current_admin()
    project = Project.query.get(project_id)
    
    if not project or project.owner_admin_id != admin.id:
        abort(404)
    
    pairs = ProjectPair.query.filter_by(project_id=project.id).order_by(ProjectPair.pair_index.asc()).all()
    
    return render_template("user/success.html", 
                         project=project, 
                         pairs=pairs, 
                         user=admin,
                         is_admin=True)

@app.route("/admin/projects/<int:project_id>/qr")
@admin_required
def admin_download_project_qr(project_id):
    """Download QR code for admin project"""
    admin = current_admin()
    project = Project.query.get(project_id)
    
    if not project or project.owner_admin_id != admin.id:
        abort(404)
    
    if not project.qr_code_filename:
        abort(404)
    
    # ✅ FIX: Use ADMIN_QR_DIR instead of QR_DIR
    return send_from_directory(
        ADMIN_QR_DIR,  # ← CHANGE THIS (was QR_DIR)
        project.qr_code_filename,
        as_attachment=True
    )

@app.route("/admin/projects/delete/<int:project_id>", methods=["POST"])
@admin_required
def admin_delete_own_project(project_id):
    """Admin delete their own project"""
    admin = current_admin()
    project = Project.query.get(project_id)
    
    if not project or project.owner_admin_id != admin.id:
        abort(404)
    
    _delete_project_files_and_rows(project)
    db.session.commit()
    
    flash("Project deleted successfully.", "success")
    return redirect(url_for("admin_projects"))

# --------------------------------------------------------------------------------------------
# Error Handlers for JSON Responses
# --------------------------------------------------------------------------------------------
@app.errorhandler(404)
@app.errorhandler(500)
@app.errorhandler(Exception)
def handle_error(error):
    """Ensure all errors return JSON for API endpoints"""
    # Check if the request is for an API/detection endpoint
    if request.path.startswith('/detect') or request.path.startswith('/api'):
        error_code = 500
        if hasattr(error, 'code'):
            error_code = error.code
        
        print(f"❌ API Error at {request.path}: {str(error)}")
        
        return jsonify({
            "detected": False,
            "reason": f"Server error: {str(error)[:100]}",
            "error": True,
            "path": request.path,
            "method": request.method
        }), error_code
    
    # For regular routes, return normal error pages
    return error
# --------------------------------------------------------------------------------------------
# Main Application Entry Point
# --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    # Create application context and bootstrap database
    with app.app_context():
        # Create all tables first
        db.create_all()
        
        # Then populate with default data
        bootstrap_database()
    
    # Run the app
    app.run(host="0.0.0.0", port=5000, debug=True)