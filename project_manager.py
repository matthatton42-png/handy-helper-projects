import anthropic
import streamlit as st
import base64
import io
import json
import os
import tempfile
import uuid
from datetime import datetime, date
from PIL import Image
from fpdf import FPDF

# ── Supabase setup (gracefully disabled if not configured) ──
DB_ENABLED = False
db = None
try:
    from supabase import create_client
    _sb_url = st.secrets["SUPABASE_URL"]
    _sb_key = st.secrets["SUPABASE_KEY"]
    db = create_client(_sb_url, _sb_key)
    DB_ENABLED = True
except Exception:
    pass

# ── Cookie manager for persistent sign-in ──
_cookie_mgr = None
try:
    import extra_streamlit_components as stx
    _cookie_mgr = stx.CookieManager(key="hh_pm_cookies")
except Exception:
    pass

def _get_cookie(name):
    try:
        return _cookie_mgr.get(name) if _cookie_mgr else None
    except: return None

def _set_cookie(name, value, days=30):
    try:
        if _cookie_mgr:
            expires = datetime.now().replace(
                year=datetime.now().year + (1 if datetime.now().month > 11 else 0),
                month=(datetime.now().month % 12) + (0 if datetime.now().month < 12 else -11)
            )
            _cookie_mgr.set(name, value, expires_at=expires)
    except: pass

def _del_cookie(name):
    try:
        if _cookie_mgr: _cookie_mgr.delete(name)
    except: pass

# ── Auth helpers ──
def _restore_session():
    if not DB_ENABLED:
        return
    if "sb_access_token" in st.session_state:
        try:
            db.auth.set_session(
                st.session_state.sb_access_token,
                st.session_state.sb_refresh_token
            )
            return
        except Exception:
            for k in ["sb_access_token","sb_refresh_token","sb_user_id","sb_user_email"]:
                st.session_state.pop(k, None)
    if "sb_user_email" not in st.session_state:
        refresh_token = _get_cookie("hh_pm_rt")
        if refresh_token:
            try:
                res = db.auth.refresh_session(refresh_token)
                st.session_state.sb_access_token  = res.session.access_token
                st.session_state.sb_refresh_token = res.session.refresh_token
                st.session_state.sb_user_id       = res.user.id
                st.session_state.sb_user_email    = res.user.email
                _set_cookie("hh_pm_rt", res.session.refresh_token)
            except Exception:
                _del_cookie("hh_pm_rt")

def get_user():
    if not DB_ENABLED or "sb_user_email" not in st.session_state:
        return None
    return {"id": st.session_state.sb_user_id, "email": st.session_state.sb_user_email}

def do_sign_in(email, password):
    res = db.auth.sign_in_with_password({"email": email.strip(), "password": password})
    st.session_state.sb_access_token  = res.session.access_token
    st.session_state.sb_refresh_token = res.session.refresh_token
    st.session_state.sb_user_id       = res.user.id
    st.session_state.sb_user_email    = res.user.email
    _set_cookie("hh_pm_rt", res.session.refresh_token, days=30)

def do_sign_up(email, password):
    return db.auth.sign_up({"email": email.strip(), "password": password})

def do_sign_out():
    if db:
        try: db.auth.sign_out()
        except: pass
    _del_cookie("hh_pm_rt")
    for k in ["sb_access_token","sb_refresh_token","sb_user_id","sb_user_email"]:
        st.session_state.pop(k, None)

# ── Project DB helpers ──
def save_project_to_db(proj):
    user = get_user()
    if not user or not DB_ENABLED: return
    try:
        db.table("projects").upsert({
            "id": proj["id"],
            "user_id": user["id"],
            "name": proj["name"],
            "type": proj.get("type",""),
            "status": proj.get("status","Active"),
            "project_data": proj
        }).execute()
    except Exception as e:
        pass

def load_projects_from_db():
    user = get_user()
    if not user or not DB_ENABLED: return {}
    try:
        res = db.table("projects").select("*").eq("user_id", user["id"]).execute()
        return {r["id"]: r["project_data"] for r in (res.data or [])}
    except: return {}

def delete_project_from_db(project_id):
    if not DB_ENABLED: return
    try:
        db.table("projects").delete().eq("id", project_id).execute()
    except: pass

_restore_session()

st.set_page_config(
    page_title="Handy Helper — Project Manager",
    page_icon="🏗️",
    layout="wide"
)

st.markdown("""
<style>
    #MainMenu { visibility: hidden; }
    header { visibility: hidden; }
    footer { visibility: hidden; }
    [data-testid="stToolbar"] { display: none; }
    .block-container { padding: 1rem 1.5rem !important; max-width: 900px !important; margin: 0 auto !important; }

    /* Sidebar */
    [data-testid="stSidebar"] { background: #1A1612 !important; }
    [data-testid="stSidebar"] .stMarkdown h3 { color: #E8521A !important; font-size: 13px !important; letter-spacing: 2px !important; }
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] span { color: #8A7E76 !important; font-size: 12px !important; }
    [data-testid="stSidebarNav"] { display: none !important; }

    /* Buttons */
    .stButton > button {
        background: #2C2520 !important; color: #F5F0E8 !important;
        border: 1px solid rgba(232,82,26,0.3) !important; border-radius: 8px !important;
        font-size: 13px !important; width: 100% !important;
    }
    .stButton > button:hover { background: #3D3530 !important; border-color: #E8521A !important; }
    .btn-orange > div > button { background: #E8521A !important; color: white !important; border: none !important; }
    .btn-orange > div > button:hover { background: #C43E0A !important; }

    /* Cards */
    .proj-card { background: #2C2520; border: 1px solid rgba(232,82,26,0.2); border-radius: 10px; padding: 1rem; margin-bottom: 0.75rem; }
    .proj-card:hover { border-color: rgba(232,82,26,0.5); }
    .phase-card { background: #1A1612; border: 1px solid rgba(232,82,26,0.15); border-radius: 8px; padding: 0.75rem; margin-bottom: 0.5rem; }

    /* Status badges */
    .badge { display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; }
    .badge-done { background: #1a3a1a; color: #4CAF50; }
    .badge-active { background: #3a2a10; color: #E8521A; }
    .badge-blocked { background: #3a1a1a; color: #e74c3c; }
    .badge-pending { background: #2a2520; color: #8A7E76; }

    /* Text inputs */
    .stTextInput input, .stTextArea textarea, .stSelectbox > div > div, .stNumberInput input {
        background: #2C2520 !important; color: #F5F0E8 !important;
        border: 1px solid rgba(232,82,26,0.3) !important; border-radius: 8px !important;
    }
    .stTextInput label, .stTextArea label, .stSelectbox label, .stNumberInput label {
        color: #8A7E76 !important; font-size: 12px !important;
    }
    .stDateInput > div > div { background: #2C2520 !important; border-color: rgba(232,82,26,0.3) !important; }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] { background: #1A1612; border-radius: 8px; gap: 4px; padding: 4px; }
    .stTabs [data-baseweb="tab"] { color: #8A7E76 !important; border-radius: 6px !important; font-size: 12px !important; }
    .stTabs [aria-selected="true"] { background: #E8521A !important; color: white !important; }
    .stTabs [data-baseweb="tab-border"] { display: none !important; }

    /* Metrics */
    [data-testid="stMetric"] { background: #2C2520; border: 1px solid rgba(232,82,26,0.2); border-radius: 8px; padding: 0.75rem !important; }
    [data-testid="stMetricValue"] { color: #E8521A !important; font-size: 22px !important; }
    [data-testid="stMetricLabel"] { color: #8A7E76 !important; font-size: 11px !important; }

    /* Progress bar */
    .stProgress > div > div { background: #E8521A !important; }
</style>
""", unsafe_allow_html=True)

# ── Anthropic client ──
try:
    api_key = st.secrets["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)
except Exception:
    client = None

# ── Default phases by project type ──
DEFAULT_PHASES = {
    "Kitchen Renovation": ["Planning & Design","Demo & Site Prep","Rough-In (Electrical/Plumbing)","Drywall & Insulation","Cabinet Installation","Countertops","Flooring","Appliance Install","Backsplash & Finishing","Final Inspection & Punch List"],
    "Bathroom Remodel": ["Planning & Design","Demo","Rough-In (Plumbing/Electric)","Waterproofing","Tile & Shower","Vanity & Fixtures","Flooring","Painting & Finishing","Final Inspection"],
    "Home Addition": ["Planning & Permits","Foundation","Framing","Roofing","Windows & Exterior Doors","Rough-In MEP","Insulation & Drywall","Interior Finishes","Exterior Finishes","Final Inspection"],
    "Basement Finishing": ["Planning & Design","Demo & Prep","Waterproofing","Framing","Rough-In MEP","Insulation","Drywall","Flooring","Painting","Final Touches & Inspection"],
    "Roof Replacement": ["Inspection & Planning","Material Selection","Prep & Protection","Tear-Off","Decking Inspection","Underlayment","Shingle/Material Install","Flashing & Ridge","Cleanup & Inspection","Final Walkthrough"],
    "HVAC System": ["Assessment & Design","Permits","Equipment Selection","Ductwork Demo","New Ductwork Install","Unit Installation","Electrical & Controls","Testing & Balancing","Final Inspection","Walkthrough & Training"],
    "Full Home Renovation": ["Assessment & Planning","Permits","Demo","Foundation/Structure","Rough-In MEP","Insulation","Drywall","Flooring","Cabinets & Millwork","Paint & Finishes","Fixtures & Appliances","Final Inspection"],
    "Custom Project": ["Phase 1","Phase 2","Phase 3","Final Inspection"],
}

STATUS_COLORS = {
    "Not Started": "badge-pending",
    "In Progress": "badge-active",
    "Complete": "badge-done",
    "Blocked": "badge-blocked",
}

# ── Session State Init ──
def init_state():
    if "projects" not in st.session_state:
        st.session_state.projects = {}
    if "active_project_id" not in st.session_state:
        st.session_state.active_project_id = None
    if "page" not in st.session_state:
        st.session_state.page = "home"
    if "active_phase_idx" not in st.session_state:
        st.session_state.active_phase_idx = 0
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

init_state()

# ── Helper functions ──
def new_project(name, proj_type, address, budget, contractor, contractor_phone, contractor_email, start_date, est_end):
    pid = str(uuid.uuid4())[:8]
    phases = []
    for ph_name in DEFAULT_PHASES.get(proj_type, ["Phase 1"]):
        phases.append({
            "id": str(uuid.uuid4())[:8],
            "name": ph_name,
            "status": "Not Started",
            "start_date": "",
            "end_date": "",
            "notes": [],
            "photos": [],
            "budget": 0,
            "actual_cost": 0,
            "checklist": [],
            "ai_insights": "",
        })
    return {
        "id": pid,
        "name": name,
        "type": proj_type,
        "address": address,
        "budget": budget,
        "contractor": contractor,
        "contractor_phone": contractor_phone,
        "contractor_email": contractor_email,
        "start_date": str(start_date),
        "est_end": str(est_end),
        "created": datetime.now().strftime("%B %d, %Y"),
        "status": "Active",
        "phases": phases,
        "pre_construction": {"questions_generated": False, "questions": [], "notes": "", "permits": []},
        "budget_items": [],
        "project_notes": [],
        "ai_conversations": [],
    }

def get_project():
    pid = st.session_state.active_project_id
    return st.session_state.projects.get(pid)

def save_project(proj):
    st.session_state.projects[proj["id"]] = proj
    save_project_to_db(proj)

def compress_image(file_bytes, max_size_mb=1):
    MAX = max_size_mb * 1024 * 1024
    img = Image.open(io.BytesIO(file_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > 1024 or img.height > 1024:
        img.thumbnail((1024, 1024), Image.LANCZOS)
    for quality in [75, 60, 45, 30]:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if len(buf.getvalue()) <= MAX:
            return base64.b64encode(buf.getvalue()).decode()
    buf = io.BytesIO()
    img.thumbnail((640, 640), Image.LANCZOS)
    img.save(buf, format="JPEG", quality=40)
    return base64.b64encode(buf.getvalue()).decode()

def safe(text):
    return (str(text)
            .replace("\u2014", "-").replace("\u2013", "-")
            .replace("\u2018", "'").replace("\u2019", "'")
            .replace("\u201C", '"').replace("\u201D", '"')
            .replace("\u2022", "*").replace("\u00e9", "e")
            .encode("latin-1", errors="replace").decode("latin-1"))

def phase_progress(proj):
    total = len(proj["phases"])
    done = sum(1 for p in proj["phases"] if p["status"] == "Complete")
    return done, total

def budget_summary(proj):
    total_budget = proj.get("budget", 0)
    spent = sum(float(item.get("actual", 0)) for item in proj.get("budget_items", []))
    phase_spent = sum(float(ph.get("actual_cost", 0)) for ph in proj.get("phases", []))
    return total_budget, max(spent, phase_spent)

def call_claude(prompt, system="You are a helpful home construction project management assistant."):
    if not client:
        return "AI not available — check your API key in Streamlit secrets."
    response = client.messages.create(
        model="claude-opus-4-6", max_tokens=1500,
        system=system, messages=[{"role": "user", "content": prompt}]
    )
    return "".join(b.text for b in response.content if hasattr(b, "text"))

def call_claude_vision(prompt, image_b64, system="You are a construction quality inspector."):
    if not client:
        return "AI not available."
    response = client.messages.create(
        model="claude-opus-4-6", max_tokens=800, system=system,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": prompt}
        ]}]
    )
    return "".join(b.text for b in response.content if hasattr(b, "text"))

def export_project_json(proj):
    export = {k: v for k, v in proj.items() if k != "phases"}
    export["phases"] = []
    for ph in proj["phases"]:
        ph_export = {k: v for k, v in ph.items() if k != "photos"}
        ph_export["photo_count"] = len(ph.get("photos", []))
        export["phases"].append(ph_export)
    return json.dumps(export, indent=2, default=str)

def generate_pdf_report(proj):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    done, total = phase_progress(proj)
    total_budget, spent = budget_summary(proj)

    # Cover page
    pdf.add_page()
    pdf.set_fill_color(26, 22, 18)
    pdf.rect(0, 0, 210, 60, 'F')
    pdf.set_xy(15, 8)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(232, 82, 26)
    pdf.cell(0, 10, "HANDY", ln=False)
    pdf.set_text_color(245, 240, 232)
    pdf.cell(0, 10, "HELPER", ln=True)
    pdf.set_xy(15, 20)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(255, 122, 69)
    pdf.cell(0, 5, "PROJECT MANAGER", ln=True)
    pdf.set_xy(15, 32)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(245, 240, 232)
    pdf.cell(0, 5, safe(f"Project Report: {proj['name']}"), ln=True)
    pdf.set_xy(15, 40)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(138, 126, 118)
    pdf.cell(0, 5, safe(f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"), ln=True)

    # Project info
    pdf.set_y(75)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(26, 22, 18)
    pdf.cell(0, 8, "PROJECT INFORMATION", ln=True)
    pdf.set_draw_color(232, 82, 26)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(4)
    fields = [
        ("Project Name", proj["name"]), ("Project Type", proj["type"]),
        ("Address", proj.get("address", "")), ("Status", proj.get("status", "")),
        ("Start Date", proj.get("start_date", "")), ("Est. Completion", proj.get("est_end", "")),
        ("Contractor", proj.get("contractor", "")), ("Contractor Phone", proj.get("contractor_phone", "")),
        ("Total Budget", f"${total_budget:,.2f}"), ("Amount Spent", f"${spent:,.2f}"),
        ("Phases Complete", f"{done} of {total}"),
    ]
    pdf.set_font("Helvetica", "", 10)
    for label, value in fields:
        pdf.set_text_color(100, 100, 100)
        pdf.cell(55, 7, safe(label + ":"), ln=False)
        pdf.set_text_color(26, 22, 18)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, safe(str(value)), ln=True)
        pdf.set_font("Helvetica", "", 10)

    # Pre-construction notes
    pre = proj.get("pre_construction", {})
    if pre.get("notes") or pre.get("questions"):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(26, 22, 18)
        pdf.cell(0, 8, "PRE-CONSTRUCTION PLANNING", ln=True)
        pdf.set_draw_color(232, 82, 26)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(4)
        if pre.get("questions"):
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(232, 82, 26)
            pdf.cell(0, 6, "Questions for Contractor:", ln=True)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(60, 60, 60)
            for q in pre["questions"][:20]:
                pdf.multi_cell(0, 5, safe(f"- {q}"))
        if pre.get("notes"):
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(232, 82, 26)
            pdf.cell(0, 6, "Notes:", ln=True)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(60, 60, 60)
            pdf.multi_cell(0, 5, safe(pre["notes"]))

    # Phases
    for i, ph in enumerate(proj["phases"], 1):
        pdf.add_page()
        pdf.set_fill_color(232, 82, 26)
        pdf.rect(0, 0, 210, 16, 'F')
        pdf.set_xy(15, 4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 8, safe(f"PHASE {i}: {ph['name'].upper()}  |  Status: {ph['status']}"), ln=True)
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(26, 22, 18)
        info = []
        if ph.get("start_date"): info.append(f"Start: {ph['start_date']}")
        if ph.get("end_date"): info.append(f"End: {ph['end_date']}")
        if ph.get("budget"): info.append(f"Budget: ${float(ph['budget']):,.2f}")
        if ph.get("actual_cost"): info.append(f"Actual: ${float(ph['actual_cost']):,.2f}")
        if info:
            pdf.cell(0, 6, safe("  |  ".join(info)), ln=True)
            pdf.ln(2)
        if ph.get("notes"):
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(232, 82, 26)
            pdf.cell(0, 6, "Notes:", ln=True)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(60, 60, 60)
            for note in ph["notes"]:
                pdf.multi_cell(0, 5, safe(f"[{note.get('date','')}] {note.get('text','')}"))
        if ph.get("photos"):
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(232, 82, 26)
            pdf.cell(0, 6, f"Photos ({len(ph['photos'])} attached):", ln=True)
            tmp_files = []
            for photo in ph["photos"][:4]:
                try:
                    img_bytes = base64.b64decode(photo["data"])
                    img = Image.open(io.BytesIO(img_bytes))
                    if img.mode != "RGB": img = img.convert("RGB")
                    img.thumbnail((700, 700), Image.LANCZOS)
                    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                    img.save(tmp.name, "JPEG", quality=75)
                    tmp.close()
                    tmp_files.append(tmp.name)
                    pdf.image(tmp.name, x=15, w=160)
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.set_text_color(100, 100, 100)
                    pdf.cell(0, 4, safe(f"  {photo.get('caption','Untitled')} — {photo.get('timestamp','')}"), ln=True)
                    pdf.ln(2)
                except Exception:
                    pass
            for f in tmp_files:
                try: os.unlink(f)
                except: pass

    # Budget
    if proj.get("budget_items"):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(26, 22, 18)
        pdf.cell(0, 8, "BUDGET SUMMARY", ln=True)
        pdf.set_draw_color(232, 82, 26)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(26, 22, 18)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(70, 7, "Description", fill=True)
        pdf.cell(30, 7, "Category", fill=True)
        pdf.cell(30, 7, "Estimated", fill=True)
        pdf.cell(30, 7, "Actual", fill=True)
        pdf.cell(20, 7, "Paid", fill=True, ln=True)
        pdf.set_font("Helvetica", "", 9)
        total_est = total_act = 0
        for idx, item in enumerate(proj["budget_items"]):
            bg = 0xFFF3EE if idx % 2 == 0 else 0xFFFFFF
            pdf.set_fill_color((bg >> 16) & 0xFF, (bg >> 8) & 0xFF, bg & 0xFF)
            pdf.set_text_color(26, 22, 18)
            est = float(item.get("estimated", 0))
            act = float(item.get("actual", 0))
            total_est += est
            total_act += act
            pdf.cell(70, 6, safe(item.get("description","")[:40]), fill=True)
            pdf.cell(30, 6, safe(item.get("category","")), fill=True)
            pdf.cell(30, 6, f"${est:,.0f}", fill=True)
            pdf.cell(30, 6, f"${act:,.0f}", fill=True)
            pdf.cell(20, 6, "Yes" if item.get("paid") else "No", fill=True, ln=True)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(70, 7, "TOTAL")
        pdf.cell(30, 7, "")
        pdf.cell(30, 7, f"${total_est:,.0f}")
        pdf.cell(30, 7, f"${total_act:,.0f}", ln=True)

    return bytes(pdf.output())

# ═══════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════
def sidebar():
    with st.sidebar:
        st.markdown("""
            <div style="padding:1rem 0.5rem 0.5rem; border-bottom:1px solid rgba(232,82,26,0.2); margin-bottom:1rem;">
                <div style="font-size:22px; font-weight:900; color:#F5F0E8;">🏗️ HANDY HELPER</div>
                <div style="font-size:10px; color:#E8521A; letter-spacing:2px; font-family:monospace;">PROJECT MANAGER</div>
            </div>
        """, unsafe_allow_html=True)

        if st.button("🏠  All Projects", key="nav_home"):
            st.session_state.page = "home"
            st.rerun()
        if st.button("➕  New Project", key="nav_new"):
            st.session_state.page = "new"
            st.rerun()

        proj = get_project()
        if proj:
            st.markdown(f"""
                <div style="margin:1rem 0 0.5rem; padding:0.75rem 0.5rem; background:rgba(232,82,26,0.08);
                    border-radius:8px; border-left:3px solid #E8521A;">
                    <div style="font-size:11px; color:#E8521A; font-family:monospace; letter-spacing:1px;">ACTIVE PROJECT</div>
                    <div style="font-size:13px; font-weight:700; color:#F5F0E8; margin-top:4px;">{proj['name']}</div>
                    <div style="font-size:10px; color:#8A7E76;">{proj['type']}</div>
                </div>
            """, unsafe_allow_html=True)
            for page, label in [
                ("dashboard", "📋  Dashboard"),
                ("pre_construction", "📝  Pre-Construction"),
                ("phases", "🔧  Project Phases"),
                ("budget", "💰  Budget Tracker"),
                ("photos", "📸  Photo Gallery"),
                ("ai_chat", "💬  AI Assistant"),
                ("export", "📄  Export Report"),
            ]:
                active = "style='color:#E8521A;'" if st.session_state.page == page else ""
                if st.button(label, key=f"nav_{page}"):
                    st.session_state.page = page
                    st.rerun()

        # ── Account & Save/Load ──
        st.markdown("---")
        user = get_user()
        if DB_ENABLED:
            if user:
                st.markdown(f"""
                    <div style="background:rgba(232,82,26,0.08); border:1px solid rgba(232,82,26,0.25);
                        border-radius:8px; padding:0.6rem 0.75rem; margin-bottom:0.5rem;">
                        <div style="font-size:10px; color:#E8521A; font-family:monospace; letter-spacing:1px;">SIGNED IN</div>
                        <div style="font-size:11px; color:#F5F0E8; margin-top:2px; word-break:break-all;">{user['email']}</div>
                        <div style="font-size:10px; color:#8A7E76; margin-top:2px;">☁️ Projects auto-save to cloud</div>
                    </div>
                """, unsafe_allow_html=True)
                if st.button("Sign Out", key="pm_signout", use_container_width=True):
                    do_sign_out()
                    st.session_state.projects = {}
                    st.session_state.active_project_id = None
                    st.session_state.page = "home"
                    st.rerun()
                if st.button("☁️ Load My Projects", key="pm_load_cloud", use_container_width=True):
                    cloud_projects = load_projects_from_db()
                    st.session_state.projects.update(cloud_projects)
                    st.success(f"Loaded {len(cloud_projects)} project(s)!")
                    st.rerun()
            else:
                st.markdown('<div style="font-size:10px; color:#8A7E76; margin-bottom:0.4rem; text-align:center;">Sign in to save projects to the cloud</div>', unsafe_allow_html=True)
                with st.expander("Sign In / Create Account"):
                    pm_mode = st.radio("", ["Sign In","Create Account"], key="pm_auth_mode", horizontal=True, label_visibility="collapsed")
                    pm_email = st.text_input("Email", key="pm_email", placeholder="you@email.com", label_visibility="collapsed")
                    pm_pass  = st.text_input("Password", key="pm_pass", type="password", placeholder="Password", label_visibility="collapsed")
                    if pm_mode == "Create Account":
                        pm_pass2 = st.text_input("Confirm", key="pm_pass2", type="password", placeholder="Confirm password", label_visibility="collapsed")
                    if st.button("Continue →", key="pm_auth_btn", use_container_width=True):
                        if not pm_email or not pm_pass:
                            st.error("Enter email and password.")
                        elif pm_mode == "Create Account":
                            if pm_pass != pm_pass2:
                                st.error("Passwords don't match.")
                            else:
                                try:
                                    do_sign_up(pm_email, pm_pass)
                                    st.success("Check your email to confirm your account, then sign in.")
                                except Exception as e:
                                    st.error(f"Error: {e}")
                        else:
                            try:
                                do_sign_in(pm_email, pm_pass)
                                cloud = load_projects_from_db()
                                st.session_state.projects.update(cloud)
                                st.success(f"Signed in! Loaded {len(cloud)} project(s).")
                                st.rerun()
                            except Exception as e:
                                st.error("Sign in failed — check your credentials.")

        st.markdown("<div style='font-size:10px; color:#8A7E76; letter-spacing:1px; margin-top:0.5rem;'>LOCAL BACKUP</div>", unsafe_allow_html=True)
        if st.session_state.projects:
            all_data = json.dumps(
                {pid: p for pid, p in st.session_state.projects.items()},
                indent=2, default=str
            )
            st.download_button("💾  Download Backup", data=all_data,
                file_name=f"handy_helper_projects_{datetime.now().strftime('%Y%m%d')}.json",
                mime="application/json", use_container_width=True)
        uploaded = st.file_uploader("📂  Restore from Backup", type=["json"], label_visibility="collapsed", key="proj_upload")
        if uploaded:
            try:
                data = json.loads(uploaded.read())
                st.session_state.projects.update(data)
                if get_user():
                    for proj in data.values():
                        save_project_to_db(proj)
                st.success("Projects restored!")
                st.rerun()
            except Exception as e:
                st.error(f"Error loading: {e}")

# ═══════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════

def page_home():
    st.markdown("## 🏗️ My Projects")
    if not st.session_state.projects:
        st.markdown("""
            <div style="text-align:center; padding:3rem; background:#2C2520; border-radius:12px;
                border:1px dashed rgba(232,82,26,0.3);">
                <div style="font-size:48px;">🏠</div>
                <div style="font-size:18px; font-weight:700; color:#F5F0E8; margin:1rem 0 0.5rem;">No Projects Yet</div>
                <div style="font-size:13px; color:#8A7E76;">Create your first project to get started</div>
            </div>
        """, unsafe_allow_html=True)
        return

    for pid, proj in st.session_state.projects.items():
        done, total = phase_progress(proj)
        pct = int(done / total * 100) if total else 0
        total_budget, spent = budget_summary(proj)
        with st.container():
            st.markdown(f"""
                <div class="proj-card">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                        <div>
                            <div style="font-size:16px; font-weight:700; color:#F5F0E8;">{proj['name']}</div>
                            <div style="font-size:12px; color:#8A7E76; margin-top:2px;">{proj['type']} · {proj.get('address','')}</div>
                        </div>
                        <span class="badge {'badge-done' if proj['status']=='Complete' else 'badge-active'}">{proj['status']}</span>
                    </div>
                    <div style="margin-top:0.75rem; font-size:11px; color:#8A7E76;">{done}/{total} phases complete · ${spent:,.0f} of ${total_budget:,.0f} budget</div>
                    <div style="background:#1A1612; border-radius:4px; height:4px; margin-top:6px;">
                        <div style="background:#E8521A; height:4px; border-radius:4px; width:{pct}%;"></div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
            c1, c2 = st.columns([3, 1])
            with c1:
                if st.button(f"Open Project", key=f"open_{pid}"):
                    st.session_state.active_project_id = pid
                    st.session_state.page = "dashboard"
                    st.rerun()
            with c2:
                if st.button("🗑️", key=f"del_{pid}", help="Delete project"):
                    del st.session_state.projects[pid]
                    delete_project_from_db(pid)
                    if st.session_state.active_project_id == pid:
                        st.session_state.active_project_id = None
                        st.session_state.page = "home"
                    st.rerun()

def page_new():
    st.markdown("## ➕ New Project")
    with st.form("new_project_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Project Name *", placeholder="e.g. Kitchen Renovation 2026")
            proj_type = st.selectbox("Project Type *", list(DEFAULT_PHASES.keys()))
            address = st.text_input("Property Address", placeholder="123 Main St, Cincinnati OH")
            budget = st.number_input("Total Budget ($)", min_value=0, value=25000, step=1000)
        with c2:
            contractor = st.text_input("Contractor / Company", placeholder="ABC Construction")
            contractor_phone = st.text_input("Contractor Phone", placeholder="513-555-0100")
            contractor_email = st.text_input("Contractor Email", placeholder="contractor@email.com")
            start_date = st.date_input("Estimated Start Date", value=date.today())
            est_end = st.date_input("Estimated Completion Date", value=date.today())

        submitted = st.form_submit_button("🚀 Create Project", use_container_width=True)
        if submitted:
            if not name.strip():
                st.error("Please enter a project name.")
            else:
                proj = new_project(name.strip(), proj_type, address, budget,
                                   contractor, contractor_phone, contractor_email,
                                   start_date, est_end)
                st.session_state.projects[proj["id"]] = proj
                st.session_state.active_project_id = proj["id"]
                st.session_state.page = "dashboard"
                st.success(f"Project '{name}' created with {len(proj['phases'])} phases!")
                st.rerun()

def page_dashboard():
    proj = get_project()
    if not proj: return st.error("No project selected.")
    done, total = phase_progress(proj)
    total_budget, spent = budget_summary(proj)
    remaining = total_budget - spent

    st.markdown(f"## 📋 {proj['name']}")
    st.markdown(f"<div style='font-size:12px; color:#8A7E76;'>{proj['type']} · {proj.get('address','')}</div>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Phases Done", f"{done}/{total}")
    c2.metric("Total Budget", f"${total_budget:,.0f}")
    c3.metric("Spent", f"${spent:,.0f}")
    c4.metric("Remaining", f"${remaining:,.0f}", delta=f"{'Under' if remaining >= 0 else 'Over'} budget")

    st.markdown(f"""
        <div style="margin:1rem 0 0.5rem; font-size:12px; color:#8A7E76;">
            Overall Progress — {int(done/total*100) if total else 0}% complete
        </div>
    """, unsafe_allow_html=True)
    st.progress(done / total if total else 0)

    st.markdown("### Project Phases")
    for i, ph in enumerate(proj["phases"]):
        color_class = STATUS_COLORS.get(ph["status"], "badge-pending")
        photos_count = len(ph.get("photos", []))
        notes_count = len(ph.get("notes", []))
        col1, col2, col3 = st.columns([4, 2, 2])
        with col1:
            st.markdown(f"""
                <div class="phase-card">
                    <span style="font-size:13px; font-weight:600; color:#F5F0E8;">{i+1}. {ph['name']}</span>
                    <span class="badge {color_class}" style="margin-left:8px;">{ph['status']}</span>
                    <div style="font-size:11px; color:#8A7E76; margin-top:4px;">
                        📸 {photos_count} photos · 📝 {notes_count} notes
                        {f"· 💰 ${float(ph.get('actual_cost',0)):,.0f}" if ph.get('actual_cost') else ''}
                    </div>
                </div>
            """, unsafe_allow_html=True)
        with col2:
            new_status = st.selectbox("", ["Not Started","In Progress","Complete","Blocked"],
                index=["Not Started","In Progress","Complete","Blocked"].index(ph["status"]),
                key=f"dash_status_{i}", label_visibility="collapsed")
            if new_status != ph["status"]:
                proj["phases"][i]["status"] = new_status
                if new_status == "Complete" and not ph.get("end_date"):
                    proj["phases"][i]["end_date"] = datetime.now().strftime("%Y-%m-%d")
                save_project(proj)
                st.rerun()
        with col3:
            if st.button(f"Open Phase", key=f"goto_phase_{i}"):
                st.session_state.active_phase_idx = i
                st.session_state.page = "phases"
                st.rerun()

    # Project notes
    st.markdown("### Project Notes")
    note_input = st.text_area("Add a project-level note", height=60, label_visibility="collapsed",
                               placeholder="Add a general project note...", key="proj_note_input")
    if st.button("Add Note", key="add_proj_note"):
        if note_input.strip():
            proj["project_notes"].append({"text": note_input.strip(), "date": datetime.now().strftime("%B %d, %Y %I:%M %p")})
            save_project(proj)
            st.rerun()
    for note in reversed(proj.get("project_notes", [])):
        st.markdown(f"""
            <div style="background:#1A1612; border-left:3px solid #E8521A; padding:0.5rem 0.75rem;
                border-radius:0 6px 6px 0; margin-bottom:0.4rem;">
                <div style="font-size:12px; color:#F5F0E8;">{note['text']}</div>
                <div style="font-size:10px; color:#8A7E76; margin-top:2px;">{note['date']}</div>
            </div>
        """, unsafe_allow_html=True)

def page_pre_construction():
    proj = get_project()
    if not proj: return st.error("No project selected.")
    pre = proj.setdefault("pre_construction", {"questions_generated": False, "questions": [], "notes": "", "permits": []})
    st.markdown("## 📝 Pre-Construction Planning")
    st.markdown(f"<div style='font-size:12px; color:#8A7E76;'>Project: {proj['name']} · {proj['type']}</div>", unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["🤖 AI Questions Generator", "✅ Planning Checklist", "📝 Notes & Permits"])

    with tab1:
        st.markdown("""
            <div style="background:#2C2520; border:1px solid rgba(232,82,26,0.2); border-radius:8px; padding:1rem; margin-bottom:1rem;">
                <div style="font-size:13px; font-weight:600; color:#F5F0E8; margin-bottom:4px;">🤖 AI Question Generator</div>
                <div style="font-size:11px; color:#8A7E76;">Generate a comprehensive list of questions to ask your contractor before work begins — covering quality, timeline, materials, costs, and protection.</div>
            </div>
        """, unsafe_allow_html=True)
        extra_context = st.text_area("Any specific concerns or details to include?",
                                      placeholder="e.g. We have pets, we're on a tight timeline, we want to keep original hardwood floors...",
                                      height=60, key="pre_context")
        if st.button("🤖 Generate Questions for " + proj["type"], key="gen_questions"):
            with st.spinner("Generating comprehensive contractor questions..."):
                prompt = f"""
I'm a homeowner about to begin a {proj['type']} project at {proj.get('address','my home')}.
Budget: ${proj.get('budget', 0):,.0f}. Contractor: {proj.get('contractor','TBD')}.
Additional context: {extra_context or 'None provided'}

Generate 30-40 specific, practical questions I should ask my contractor before work begins,
organized into these categories:
1. Experience & Qualifications (5-7 questions)
2. Timeline & Scheduling (5-6 questions)
3. Materials & Products (4-5 questions)
4. Cost & Payment (5-6 questions)
5. Project Management & Communication (4-5 questions)
6. Site Protection & Safety (3-4 questions)
7. Permits & Code Compliance (3-4 questions)
8. Warranties & Guarantees (3-4 questions)
9. Specific to {proj['type']} (4-5 questions)

Format each question on its own line starting with a dash (-). Include category headers.
Be specific and practical — questions a knowledgeable homeowner would ask.
"""
                result = call_claude(prompt)
                lines = [l.strip().lstrip("-").strip() for l in result.split("\n") if l.strip() and not l.strip().startswith("#")]
                pre["questions"] = [l for l in lines if len(l) > 10]
                pre["questions_generated"] = True
                save_project(proj)
                st.rerun()

        if pre.get("questions"):
            st.markdown(f"**{len(pre['questions'])} questions generated:**")
            current_cat = ""
            for q in pre["questions"]:
                if any(q.endswith(":") or q.isupper() for _ in [1]):
                    if q.endswith(":") or (len(q) < 60 and q == q.upper()):
                        st.markdown(f"**{q}**")
                        continue
                st.markdown(f"• {q}")
            st.download_button(
                "📥 Download Questions as Text",
                data="\n".join(pre["questions"]),
                file_name=f"{proj['name']}_contractor_questions.txt",
                mime="text/plain"
            )

    with tab2:
        st.markdown("### Pre-Construction Checklist")
        default_checklist = [
            "Signed contract with scope of work", "Payment schedule agreed",
            "Permits pulled and approved", "Start date confirmed in writing",
            "Material selections finalized", "Site protection plan discussed",
            "Communication schedule set (daily/weekly updates)", "Emergency contact established",
            "Lien waiver process agreed", "Cleanup responsibilities defined",
            "Neighbors notified if needed", "HOA approval obtained if required",
        ]
        checklist = pre.setdefault("checklist", [{"item": i, "done": False} for i in default_checklist])
        for idx, item in enumerate(checklist):
            col1, col2 = st.columns([8, 2])
            with col1:
                done = st.checkbox(item["item"], value=item["done"], key=f"pre_check_{idx}")
            if done != item["done"]:
                pre["checklist"][idx]["done"] = done
                save_project(proj)
        new_item = st.text_input("Add checklist item", placeholder="Add a custom checklist item...", key="new_check_item")
        if st.button("Add Item", key="add_check") and new_item.strip():
            pre["checklist"].append({"item": new_item.strip(), "done": False})
            save_project(proj)
            st.rerun()

    with tab3:
        st.markdown("### Notes")
        notes_val = st.text_area("Pre-construction notes", value=pre.get("notes", ""),
                                   height=120, key="pre_notes_input",
                                   placeholder="Meeting notes, decisions made, things to remember...")
        if st.button("Save Notes", key="save_pre_notes"):
            pre["notes"] = notes_val
            save_project(proj)
            st.success("Saved!")

        st.markdown("### Permits Required")
        permits = pre.setdefault("permits", [])
        c1, c2, c3 = st.columns(3)
        with c1: permit_name = st.text_input("Permit Type", placeholder="Building Permit", key="perm_name")
        with c2: permit_status = st.selectbox("Status", ["Needed","Applied","Approved","Not Required"], key="perm_status")
        with c3: permit_num = st.text_input("Permit #", placeholder="Optional", key="perm_num")
        if st.button("Add Permit", key="add_permit") and permit_name.strip():
            permits.append({"name": permit_name, "status": permit_status, "number": permit_num})
            save_project(proj)
            st.rerun()
        for p in permits:
            st.markdown(f"• **{p['name']}** — {p['status']}" + (f" (#{p['number']})" if p.get("number") else ""))

def page_phases():
    proj = get_project()
    if not proj: return st.error("No project selected.")
    st.markdown("## 🔧 Project Phases")
    idx = st.session_state.get("active_phase_idx", 0)
    if idx >= len(proj["phases"]): idx = 0

    # Phase selector
    phase_names = [f"{i+1}. {p['name']} [{p['status'][:2]}]" for i, p in enumerate(proj["phases"])]
    selected = st.selectbox("Select Phase", phase_names, index=idx, key="phase_selector")
    idx = phase_names.index(selected)
    st.session_state.active_phase_idx = idx
    ph = proj["phases"][idx]

    st.markdown(f"""
        <div style="background:#2C2520; border:1px solid rgba(232,82,26,0.3); border-radius:10px;
            padding:1rem; margin-bottom:1rem;">
            <div style="font-size:15px; font-weight:700; color:#F5F0E8;">{ph['name']}</div>
            <span class="badge {STATUS_COLORS.get(ph['status'],'badge-pending')}">{ph['status']}</span>
        </div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📋 Details", "📝 Notes", "📸 Photos", "🤖 AI Guidance", "✅ Checklist"])

    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            new_status = st.selectbox("Status", ["Not Started","In Progress","Complete","Blocked"],
                index=["Not Started","In Progress","Complete","Blocked"].index(ph["status"]), key="ph_status")
            ph_budget = st.number_input("Phase Budget ($)", value=float(ph.get("budget",0)), step=100.0, key="ph_budget")
        with c2:
            ph_actual = st.number_input("Actual Cost ($)", value=float(ph.get("actual_cost",0)), step=100.0, key="ph_actual")
            ph_start = st.text_input("Start Date", value=ph.get("start_date",""), placeholder="YYYY-MM-DD", key="ph_start")
            ph_end = st.text_input("End Date / Expected End", value=ph.get("end_date",""), placeholder="YYYY-MM-DD", key="ph_end")
        if st.button("💾 Save Phase Details", key="save_ph_details"):
            ph["status"] = new_status
            ph["budget"] = ph_budget
            ph["actual_cost"] = ph_actual
            ph["start_date"] = ph_start
            ph["end_date"] = ph_end
            if new_status == "In Progress" and not ph.get("start_date"):
                ph["start_date"] = datetime.now().strftime("%Y-%m-%d")
            save_project(proj)
            st.success("Phase details saved!")

    with tab2:
        note_text = st.text_area("Add Note", height=80, placeholder="What happened today? Any issues, decisions, or observations?", key="ph_note")
        if st.button("Add Note", key="add_ph_note") and note_text.strip():
            ph["notes"].append({"text": note_text.strip(), "date": datetime.now().strftime("%B %d, %Y %I:%M %p")})
            save_project(proj)
            st.rerun()
        for note in reversed(ph.get("notes", [])):
            st.markdown(f"""
                <div style="background:#1A1612; border-left:3px solid #E8521A; padding:0.5rem 0.75rem;
                    border-radius:0 6px 6px 0; margin-bottom:0.4rem;">
                    <div style="font-size:12px; color:#F5F0E8;">{note['text']}</div>
                    <div style="font-size:10px; color:#8A7E76;">{note['date']}</div>
                </div>
            """, unsafe_allow_html=True)

    with tab3:
        uploaded = st.file_uploader("Upload photo(s) for this phase",
                                     type=["jpg","jpeg","png","webp"],
                                     accept_multiple_files=True, key=f"ph_photos_{idx}")
        caption_input = st.text_input("Caption for uploaded photos", placeholder="e.g. Demo complete, framing started...", key="ph_caption")
        ai_analyze = st.checkbox("🤖 Get AI analysis of uploaded photo", key="ph_ai_analyze")
        if st.button("📸 Save Photos", key="save_ph_photos") and uploaded:
            for f in uploaded:
                b64 = compress_image(f.read())
                analysis = ""
                if ai_analyze:
                    with st.spinner("AI analyzing photo..."):
                        analysis = call_claude_vision(
                            f"This photo is from the '{ph['name']}' phase of a {proj['type']} project. "
                            "Provide a brief 2-3 sentence professional description of what you see, "
                            "noting the apparent quality and any observations relevant to this phase.",
                            b64
                        )
                ph["photos"].append({
                    "name": f.name,
                    "data": b64,
                    "caption": caption_input or f.name,
                    "timestamp": datetime.now().strftime("%B %d, %Y %I:%M %p"),
                    "analysis": analysis
                })
            save_project(proj)
            st.success(f"{len(uploaded)} photo(s) saved!")
            st.rerun()

        photos = ph.get("photos", [])
        if photos:
            st.markdown(f"**{len(photos)} photo(s) in this phase:**")
            cols = st.columns(2)
            for i, photo in enumerate(photos):
                with cols[i % 2]:
                    img_bytes = base64.b64decode(photo["data"])
                    st.image(img_bytes, caption=photo.get("caption",""), use_column_width=True)
                    st.markdown(f"<div style='font-size:10px; color:#8A7E76;'>{photo.get('timestamp','')}</div>", unsafe_allow_html=True)
                    if photo.get("analysis"):
                        st.markdown(f"<div style='font-size:11px; color:#8A7E76; background:#1A1612; padding:0.4rem; border-radius:6px; margin-top:4px;'>🤖 {photo['analysis']}</div>", unsafe_allow_html=True)
                    if st.button("🗑️ Remove", key=f"del_photo_{idx}_{i}"):
                        ph["photos"].pop(i)
                        save_project(proj)
                        st.rerun()

    with tab4:
        st.markdown("### 🤖 AI Phase Guidance")
        if ph.get("ai_insights"):
            st.markdown(f"""
                <div style="background:#1A1612; border:1px solid rgba(232,82,26,0.2); border-radius:8px; padding:1rem; margin-bottom:1rem;">
                    <div style="font-size:11px; color:#E8521A; margin-bottom:0.5rem;">AI GUIDANCE</div>
                    <div style="font-size:13px; color:#F5F0E8;">{ph['ai_insights']}</div>
                </div>
            """, unsafe_allow_html=True)
        if st.button("🤖 Generate Phase Guidance", key="gen_phase_ai"):
            with st.spinner(f"Getting AI guidance for {ph['name']}..."):
                notes_summary = " | ".join([n["text"] for n in ph.get("notes",[])][:5]) or "No notes yet"
                prompt = f"""
For a {proj['type']} project, the homeowner is currently in the '{ph['name']}' phase.
Project notes so far: {notes_summary}

Please provide:
1. What typically happens in this phase and what the homeowner should expect (3-4 sentences)
2. What questions to ask the contractor RIGHT NOW (5-6 specific questions)
3. What to watch for / red flags in this phase (4-5 specific items)
4. What the homeowner should document/photograph before this phase ends (4-5 items)
5. What should be complete before moving to the next phase (3-4 checkpoints)

Be specific to {ph['name']} in a {proj['type']} context.
"""
                result = call_claude(prompt)
                ph["ai_insights"] = result
                save_project(proj)
                st.rerun()

    with tab5:
        checklist = ph.setdefault("checklist", [])
        st.markdown("### Phase Checklist")
        for cidx, item in enumerate(checklist):
            col1, col2 = st.columns([8, 1])
            with col1:
                done = st.checkbox(item["item"], value=item["done"], key=f"ph_check_{idx}_{cidx}")
            with col2:
                if st.button("✕", key=f"del_check_{idx}_{cidx}"):
                    checklist.pop(cidx)
                    save_project(proj)
                    st.rerun()
            if done != item["done"]:
                checklist[cidx]["done"] = done
                save_project(proj)
        new_item = st.text_input("Add checklist item", key=f"new_item_{idx}", placeholder="Add a task...")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Add Item", key=f"add_item_{idx}") and new_item.strip():
                checklist.append({"item": new_item.strip(), "done": False})
                save_project(proj)
                st.rerun()
        with c2:
            if st.button("🤖 AI Generate Checklist", key=f"ai_checklist_{idx}"):
                with st.spinner("Generating checklist..."):
                    result = call_claude(f"For the '{ph['name']}' phase of a {proj['type']} project, generate 10-15 specific checklist items a homeowner should verify are complete before considering this phase done. One item per line, starting with a dash (-).")
                    items = [l.strip().lstrip("-").strip() for l in result.split("\n") if l.strip().startswith("-")]
                    for item in items:
                        checklist.append({"item": item, "done": False})
                    save_project(proj)
                    st.rerun()

def page_budget():
    proj = get_project()
    if not proj: return st.error("No project selected.")
    st.markdown("## 💰 Budget Tracker")
    total_budget, spent = budget_summary(proj)
    remaining = total_budget - spent
    over = spent > total_budget

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Budget", f"${total_budget:,.0f}")
    c2.metric("Total Spent", f"${spent:,.0f}")
    c3.metric("Remaining", f"${remaining:,.0f}")
    c4.metric("Status", "Over Budget ⚠️" if over else "On Track ✓")
    st.progress(min(spent / total_budget, 1.0) if total_budget else 0)

    st.markdown("### Add Budget Item")
    with st.form("add_budget"):
        c1, c2 = st.columns(2)
        with c1:
            desc = st.text_input("Description *", placeholder="Demo labor")
            category = st.selectbox("Category", ["Labor","Materials","Permits & Fees","Design","Equipment","Contingency","Other"])
        with c2:
            estimated = st.number_input("Estimated ($)", min_value=0.0, step=100.0)
            actual = st.number_input("Actual Paid ($)", min_value=0.0, step=100.0)
            paid = st.checkbox("Paid")
        if st.form_submit_button("Add Item") and desc.strip():
            proj["budget_items"].append({
                "description": desc, "category": category,
                "estimated": estimated, "actual": actual, "paid": paid,
                "date": datetime.now().strftime("%Y-%m-%d")
            })
            save_project(proj)
            st.rerun()

    items = proj.get("budget_items", [])
    if items:
        st.markdown(f"### Budget Items ({len(items)})")
        total_est = sum(float(i.get("estimated",0)) for i in items)
        total_act = sum(float(i.get("actual",0)) for i in items)
        for idx, item in enumerate(items):
            c1, c2, c3, c4, c5 = st.columns([4, 2, 2, 2, 1])
            c1.markdown(f"<div style='font-size:12px; color:#F5F0E8;'>{item['description']}</div><div style='font-size:10px; color:#8A7E76;'>{item['category']}</div>", unsafe_allow_html=True)
            c2.markdown(f"<div style='font-size:12px; color:#8A7E76;'>Est: ${float(item.get('estimated',0)):,.0f}</div>", unsafe_allow_html=True)
            c3.markdown(f"<div style='font-size:12px; color:#F5F0E8;'>Actual: ${float(item.get('actual',0)):,.0f}</div>", unsafe_allow_html=True)
            c4.markdown(f"<div style='font-size:11px; color:{'#4CAF50' if item.get('paid') else '#E8521A'};'>{'✓ Paid' if item.get('paid') else '⏳ Unpaid'}</div>", unsafe_allow_html=True)
            with c5:
                if st.button("✕", key=f"del_budget_{idx}"):
                    proj["budget_items"].pop(idx)
                    save_project(proj)
                    st.rerun()
        st.markdown(f"""
            <div style="background:#2C2520; border:1px solid rgba(232,82,26,0.2); border-radius:8px;
                padding:0.75rem; margin-top:1rem; display:flex; gap:2rem;">
                <div><div style="font-size:10px; color:#8A7E76;">TOTAL ESTIMATED</div>
                     <div style="font-size:18px; font-weight:700; color:#F5F0E8;">${total_est:,.0f}</div></div>
                <div><div style="font-size:10px; color:#8A7E76;">TOTAL ACTUAL</div>
                     <div style="font-size:18px; font-weight:700; color:#E8521A;">${total_act:,.0f}</div></div>
                <div><div style="font-size:10px; color:#8A7E76;">VARIANCE</div>
                     <div style="font-size:18px; font-weight:700; color:{'#e74c3c' if total_act > total_est else '#4CAF50'};">${total_act - total_est:+,.0f}</div></div>
            </div>
        """, unsafe_allow_html=True)

def page_photos():
    proj = get_project()
    if not proj: return st.error("No project selected.")
    st.markdown("## 📸 Photo Gallery")
    all_photos = []
    for ph in proj["phases"]:
        for photo in ph.get("photos", []):
            all_photos.append({**photo, "phase": ph["name"]})
    if not all_photos:
        st.info("No photos yet. Upload photos in each phase.")
        return
    st.markdown(f"**{len(all_photos)} total photos across {len(proj['phases'])} phases**")
    filter_phase = st.selectbox("Filter by phase", ["All Phases"] + [ph["name"] for ph in proj["phases"]])
    if filter_phase != "All Phases":
        all_photos = [p for p in all_photos if p["phase"] == filter_phase]
    cols = st.columns(3)
    for i, photo in enumerate(all_photos):
        with cols[i % 3]:
            img_bytes = base64.b64decode(photo["data"])
            st.image(img_bytes, use_column_width=True)
            st.markdown(f"""
                <div style="font-size:10px; color:#8A7E76; margin-bottom:0.5rem;">
                    <strong style="color:#E8521A;">{photo['phase']}</strong><br>
                    {photo.get('caption','')}<br>
                    {photo.get('timestamp','')}
                </div>
            """, unsafe_allow_html=True)

def page_ai_chat():
    proj = get_project()
    if not proj: return st.error("No project selected.")
    st.markdown("## 💬 AI Project Assistant")
    st.markdown(f"<div style='font-size:11px; color:#8A7E76; margin-bottom:1rem;'>AI assistant with full context of your {proj['type']} project.</div>", unsafe_allow_html=True)

    # Build project context
    done, total = phase_progress(proj)
    total_budget, spent = budget_summary(proj)
    current_phases = [ph["name"] for ph in proj["phases"] if ph["status"] == "In Progress"]
    context = f"""
PROJECT CONTEXT:
- Project: {proj['name']} ({proj['type']})
- Address: {proj.get('address','')}
- Contractor: {proj.get('contractor','')}
- Budget: ${total_budget:,.0f} total, ${spent:,.0f} spent
- Progress: {done} of {total} phases complete
- Currently In Progress: {', '.join(current_phases) or 'None'}
- Start Date: {proj.get('start_date','')}
- Est. Completion: {proj.get('est_end','')}
Recent project notes: {' | '.join([n['text'] for n in proj.get('project_notes',[])[-3:]])}
"""

    system = f"""You are an expert home construction project manager and advisor for Handy Helper.
You are assisting with a specific project. Here is the full project context:
{context}

Use this context to give specific, practical advice tailored to this exact project.
Be direct, actionable, and honest. Reference specific project details when relevant."""

    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    with st.form("ai_chat_form", clear_on_submit=True):
        user_msg = st.text_input("Ask anything about your project...",
                                   placeholder="e.g. What should I verify before the contractor pours concrete? / Are we on track with the budget?",
                                   label_visibility="collapsed", key="ai_chat_input")
        submitted = st.form_submit_button("Send →", use_container_width=True)

    if submitted and user_msg.strip():
        st.session_state.chat_history.append({"role": "user", "content": user_msg})
        msgs = []
        for m in st.session_state.chat_history:
            msgs.append({"role": m["role"], "content": m["content"]})
        with st.spinner("Thinking..."):
            response = client.messages.create(
                model="claude-opus-4-6", max_tokens=1200,
                system=system, messages=msgs
            ) if client else None
            reply = "".join(b.text for b in response.content if hasattr(b, "text")) if response else "AI not available."
        st.session_state.chat_history.append({"role": "assistant", "content": reply})
        st.rerun()

    if st.session_state.chat_history:
        if st.button("🗑️ Clear Chat", key="clear_chat"):
            st.session_state.chat_history = []
            st.rerun()

def page_export():
    proj = get_project()
    if not proj: return st.error("No project selected.")
    st.markdown("## 📄 Export Project Report")
    done, total = phase_progress(proj)
    total_budget, spent = budget_summary(proj)
    all_photos = sum(len(ph.get("photos",[])) for ph in proj["phases"])

    st.markdown(f"""
        <div style="background:#2C2520; border:1px solid rgba(232,82,26,0.2); border-radius:10px; padding:1rem; margin-bottom:1rem;">
            <div style="font-size:13px; font-weight:700; color:#F5F0E8; margin-bottom:0.5rem;">{proj['name']}</div>
            <div style="display:flex; gap:2rem; flex-wrap:wrap;">
                <div><div style="font-size:10px; color:#8A7E76;">PHASES</div><div style="font-size:16px; color:#E8521A;">{done}/{total}</div></div>
                <div><div style="font-size:10px; color:#8A7E76;">BUDGET</div><div style="font-size:16px; color:#E8521A;">${total_budget:,.0f}</div></div>
                <div><div style="font-size:10px; color:#8A7E76;">SPENT</div><div style="font-size:16px; color:#E8521A;">${spent:,.0f}</div></div>
                <div><div style="font-size:10px; color:#8A7E76;">PHOTOS</div><div style="font-size:16px; color:#E8521A;">{all_photos}</div></div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 📄 PDF Report")
        st.markdown("<div style='font-size:11px; color:#8A7E76; margin-bottom:0.5rem;'>Full project report with all phases, photos, notes, and budget summary.</div>", unsafe_allow_html=True)
        if st.button("Generate PDF Report", key="gen_pdf", use_container_width=True):
            with st.spinner("Building your project report..."):
                try:
                    pdf_bytes = generate_pdf_report(proj)
                    fname = f"HH_{proj['name'].replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    st.download_button("⬇️ Download PDF Report", data=pdf_bytes, file_name=fname, mime="application/pdf", use_container_width=True)
                except Exception as e:
                    st.error(f"PDF error: {e}")

    with c2:
        st.markdown("### 💾 Project Data (JSON)")
        st.markdown("<div style='font-size:11px; color:#8A7E76; margin-bottom:0.5rem;'>Save your project data. Use this to reload your project in a future session.</div>", unsafe_allow_html=True)
        json_data = json.dumps({proj["id"]: proj}, indent=2, default=str)
        fname = f"HH_Project_{proj['name'].replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.json"
        st.download_button("⬇️ Download Project JSON", data=json_data, file_name=fname, mime="application/json", use_container_width=True)

# ═══════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════
sidebar()
page = st.session_state.page

if page == "home":          page_home()
elif page == "new":         page_new()
elif page == "dashboard":   page_dashboard()
elif page == "pre_construction": page_pre_construction()
elif page == "phases":      page_phases()
elif page == "budget":      page_budget()
elif page == "photos":      page_photos()
elif page == "ai_chat":     page_ai_chat()
elif page == "export":      page_export()
else:                       page_home()
