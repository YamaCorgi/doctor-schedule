import streamlit as st
from dotenv import load_dotenv
import os, json, datetime, time
import google.generativeai as genai
from PIL import Image
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv()

# =============================================
# Gemini API 金鑰設定（Streamlit Cloud 用 st.secrets，本機用 .env）
# =============================================
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)

# =============================================
# 密碼設定
# =============================================
APP_PASSWORD = "Yama520"

def check_password():
    """密碼驗證，失敗則擋住 app"""
    def password_entered():
        if st.session_state.get("password_input") == APP_PASSWORD:
            st.session_state["password_correct"] = True
            # 清除密碼欄位，不留在記憶體
            del st.session_state["password_input"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    # 顯示密碼輸入畫面
    st.set_page_config(page_title="醫師排班辨識系統", page_icon="🔒")
    st.title("🔒 醫師排班辨識系統")
    st.markdown("### 請輸入密碼以進入系統")
    st.text_input(
        "密碼",
        type="password",
        key="password_input",
        on_change=password_entered,
    )
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("❌ 密碼錯誤,請重新輸入")
    st.caption("如需取得密碼,請聯絡管理員。")
    return False


if not check_password():
    st.stop()


# =============================================
# 班別時間對應表
# =============================================
SCOPES = ["https://www.googleapis.com/auth/calendar"]

SHIFT_TIMES = {
    "外科": {
        "白班": {"start": "08:00", "end": "16:00", "offset": 0},
        "午班": {"start": "16:00", "end": "23:59", "offset": 0},
        "晚班": {"start": "00:00", "end": "08:00", "offset": 1},
    },
    "內科+小兒科": {
        1: {
            "白班": {"start": "07:00", "end": "11:00"},
            "午班": {"start": "11:00", "end": "22:00"},
            "晚班": {"start": "22:00", "end": "07:00"},
        },
        2: {"白班": {"start": "07:00", "end": "14:00"}, "午班": {"start": "14:00", "end": "22:00"}, "晚班": {"start": "22:00", "end": "07:00"}},
        3: {"白班": {"start": "07:00", "end": "14:00"}, "午班": {"start": "14:00", "end": "22:00"}, "晚班": {"start": "22:00", "end": "07:00"}},
        4: {"白班": {"start": "07:00", "end": "14:00"}, "午班": {"start": "14:00", "end": "22:00"}, "晚班": {"start": "22:00", "end": "07:00"}},
        5: {"白班": {"start": "07:00", "end": "14:00"}, "午班": {"start": "14:00", "end": "22:00"}, "晚班": {"start": "22:00", "end": "07:00"}},
        6: {"白班": {"start": "07:00", "end": "13:00"}, "午班": {"start": "13:00", "end": "22:00"}, "晚班": {"start": "22:00", "end": "07:00"}},
        7: {"白班": {"start": "07:00", "end": "13:00"}, "午班": {"start": "13:00", "end": "22:00"}, "晚班": {"start": "22:00", "end": "07:00"}},
    }
}


def get_shift_time(department, date_str, shift_name):
    try:
        date_obj = datetime.date.fromisoformat(date_str)
        weekday = date_obj.isoweekday()
    except Exception:
        return None, None
    if department == "外科":
        info = SHIFT_TIMES["外科"].get(shift_name)
        if info:
            start_date = date_obj + datetime.timedelta(days=info["offset"])
            start_dt = f"{start_date.isoformat()}T{info['start']}:00"
            end_dt = f"{start_date.isoformat()}T{info['end']}:00"
            return start_dt, end_dt
    elif department == "內科+小兒科":
        day_table = SHIFT_TIMES["內科+小兒科"].get(weekday, {})
        info = day_table.get(shift_name)
        if info:
            start_dt = f"{date_str}T{info['start']}:00"
            if info["start"] > info["end"]:
                end_date = (date_obj + datetime.timedelta(days=1)).isoformat()
            else:
                end_date = date_str
            end_dt = f"{end_date}T{info['end']}:00"
            return start_dt, end_dt
    return None, None


def get_calendar_service(target_email):
    safe_email = target_email.replace("@", "_at_").replace(".", "_")
    token_file = f"token_{safe_email}.json"

    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError("雲端版本無 credentials.json,Google Calendar 功能僅本機可用")
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0, login_hint=target_email)
        with open(token_file, "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def write_to_calendar(shifts, doctor_name, target_email):
    service = get_calendar_service(target_email)
    try:
        calendar_info = service.calendars().get(calendarId="primary").execute()
        actual_email = calendar_info.get("id", "")
        if actual_email.lower() != target_email.lower():
            return 0, f"登入的帳號({actual_email})與目標({target_email})不符"
    except Exception as e:
        return 0, f"無法取得帳號資訊:{e}"

    count = 0
    for shift in shifts:
        dept = shift["department"]
        start_dt, end_dt = get_shift_time(dept, shift["date"], shift["shift"])
        if start_dt and end_dt:
            event = {
                "summary": f"{doctor_name}｜{dept}｜{shift['shift']}",
                "start": {"dateTime": start_dt, "timeZone": "Asia/Taipei"},
                "end":   {"dateTime": end_dt,   "timeZone": "Asia/Taipei"},
                "reminders": {"useDefault": False, "overrides": [
                    {"method": "email", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 24 * 60},
                ]}
            }
        else:
            event = {
                "summary": f"{doctor_name}｜{dept}｜{shift['shift']}",
                "start": {"date": shift["date"]},
                "end":   {"date": shift["date"]},
                "reminders": {"useDefault": False, "overrides": [
                    {"method": "email", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 24 * 60},
                ]}
            }
        service.events().insert(calendarId="primary", body=event).execute()
        count += 1
    return count, None


def recognize_schedule(image, doctor_name):
    model = genai.GenerativeModel(
        "gemini-2.5-flash",
        generation_config={"temperature": 0.0, "response_mime_type": "application/json"}
    )
    prompt = f"""
你正在分析一張台灣醫院的「急診醫師值班表」圖片。
標題「急診醫師值班表-115年04月」= 民國115年4月 = 西元2026年4月。

【表格結構,從左到右】
欄1:日期數字(1到月底)
欄2:星期(一到日)
欄3-5:「內科+小兒科」三欄 → 白班、午班、晚班
欄6-8:「外科」三欄 → 白班、午班、晚班

每一列(每一天)共有 6 個班別格子。每格可能寫 1 個或 2 個醫師名字。

【關鍵任務】
必須非常徹底地掃描整張表。針對每一列的每一個格子,逐一檢查是否包含「{doctor_name}」三個字。
很重要:這位醫師一個月通常排班 8~12 次,如果你只找到少於 8 筆,代表你漏看了,請再重新仔細掃描一次!

【特別注意】
- 兩人並排的格子很容易漏看第二個名字,要特別仔細
- 有些字手寫風格略有差異,只要看起來像「{doctor_name}」就算
- 內科+小兒科 和 外科 兩個區塊都要看完

【輸出規則】
- date 必須是 "2026-04-XX" 格式
- department 只能是 "內科+小兒科" 或 "外科"
- shift 只能是 "白班"、"午班" 或 "晚班"
- 依日期由小到大排序

請直接回傳 JSON 陣列。
"""
    response = model.generate_content([prompt, image])
    raw = response.text.strip().replace("```json", "").replace("```", "").strip()
    return raw


# =============================================
# 介面
# =============================================
st.set_page_config(page_title="醫師排班辨識系統", page_icon="🏥")
st.title("🏥 醫師排班辨識系統")

st.markdown("### 第一步:輸入資訊")

col1, col2 = st.columns(2)
with col1:
    doctor_name = st.text_input("醫師姓名", value="陳璿羽")
with col2:
    target_email = st.text_input(
        "目標 Gmail(行事曆寫入對象)",
        value="featherch@gmail.com",
        help="班表會寫入這個 Gmail 帳號的行事曆"
    )

uploaded_file = st.file_uploader("上傳排班表圖片", type=["jpg", "jpeg", "png"])

if uploaded_file:
    st.image(uploaded_file, caption="已上傳的排班表", use_container_width=True)

if uploaded_file and doctor_name and target_email:
    st.success(f"✅ 將辨識 {doctor_name} 醫師的班別,並寫入 `{target_email}` 的行事曆")
    if st.button("🔍 開始辨識", type="primary"):
        keys_to_del = [k for k in st.session_state.keys()
                       if k.startswith(("date_", "shift_", "dept_", "keep_")) or k == "shifts"]
        for k in keys_to_del:
            del st.session_state[k]

        status_text = st.empty()
        progress_bar = st.progress(0)
        try:
            status_text.markdown("⏳ **階段 1/4**:載入圖片中... `20%`")
            progress_bar.progress(20)
            image = Image.open(uploaded_file)
            time.sleep(0.3)

            status_text.markdown("🔗 **階段 2/4**:連線 Gemini AI... `40%`")
            progress_bar.progress(40)
            time.sleep(0.3)

            status_text.markdown("🤖 **階段 3/4**:AI 徹底掃描排班表中... `70%`")
            progress_bar.progress(70)
            raw = recognize_schedule(image, doctor_name)

            status_text.markdown("📋 **階段 4/4**:解析辨識結果... `90%`")
            progress_bar.progress(90)
            time.sleep(0.3)

            shifts = json.loads(raw)
            shifts.sort(key=lambda x: (x.get("date", ""), x.get("department", "")))

            progress_bar.progress(100)
            status_text.markdown(f"✅ **辨識完成!共找到 {len(shifts)} 筆** `100%`")
            time.sleep(0.5)
            progress_bar.empty()
            status_text.empty()

            st.session_state["shifts"] = shifts
            st.session_state["doctor_name"] = doctor_name
            st.session_state["target_email"] = target_email
            if len(shifts) < 8:
                st.warning(f"⚠️ 只找到 {len(shifts)} 筆班別,可能有遺漏。請在下方清單手動新增!")
            else:
                st.success(f"🎉 辨識完成!共找到 {len(shifts)} 筆班別")

        except json.JSONDecodeError:
            progress_bar.empty()
            status_text.empty()
            st.error("⚠️ AI 回傳格式有誤,請再試一次")
            st.code(raw)
        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.error(f"⚠️ 發生錯誤:{e}")

if "shifts" in st.session_state:
    st.markdown("---")
    st.markdown("### 第二步:確認班別清單")
    st.markdown("**⚠️ 請仔細核對,可手動修改/新增/刪除**")

    shifts = st.session_state["shifts"]
    dept_options = ["內科+小兒科", "外科"]
    shift_options = ["白班", "午班", "晚班"]

    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("➕ 新增一筆班別"):
            shifts.append({"date": "2026-04-01", "department": "內科+小兒科", "shift": "白班"})
            st.session_state["shifts"] = shifts
            st.rerun()

    edited_shifts = []
    to_delete = []

    for i, shift in enumerate(shifts):
        col1, col2, col3, col4 = st.columns([2.2, 2.3, 1.8, 1])
        date = col1.text_input("日期", value=shift.get("date", "2026-04-01"), key=f"date_{i}")

        default_dept = shift.get("department", "內科+小兒科")
        if default_dept not in dept_options:
            default_dept = "內科+小兒科"
        dept = col2.selectbox("部門", dept_options, index=dept_options.index(default_dept), key=f"dept_{i}")

        default_shift = shift.get("shift", "白班")
        if default_shift not in shift_options:
            default_shift = "白班"
        name = col3.selectbox("班別", shift_options, index=shift_options.index(default_shift), key=f"shift_{i}")

        if col4.button("🗑️ 刪除", key=f"del_{i}"):
            to_delete.append(i)

        start_dt, end_dt = get_shift_time(dept, date, name)
        if start_dt:
            same_day = start_dt[:10] == end_dt[:10]
            if same_day:
                st.caption(f"　⏰ {start_dt[:10]}　{start_dt[11:16]} ～ {end_dt[11:16]}")
            else:
                st.caption(f"　⏰ {start_dt[:10]} {start_dt[11:16]} ～ {end_dt[:10]} {end_dt[11:16]}(跨日)")
        else:
            st.caption("　⚠️ 找不到對應時間,將以全天事件寫入")

        edited_shifts.append({"date": date, "department": dept, "shift": name})

    if to_delete:
        new_shifts = [s for idx, s in enumerate(edited_shifts) if idx not in to_delete]
        st.session_state["shifts"] = new_shifts
        keys_to_del = [k for k in st.session_state.keys()
                       if k.startswith(("date_", "shift_", "dept_", "keep_", "del_"))]
        for k in keys_to_del:
            del st.session_state[k]
        st.rerun()

    st.markdown("---")
    target_email = st.session_state.get("target_email", "featherch@gmail.com")
    st.markdown(f"**📌 共 {len(edited_shifts)} 筆班別將寫入 `{target_email}` 的行事曆**")

    if st.button("✅ 確認並寫入 Google 行事曆", type="primary"):
        with st.spinner("⏳ 寫入中..."):
            try:
                count, error_msg = write_to_calendar(
                    edited_shifts,
                    st.session_state["doctor_name"],
                    target_email
                )
                if error_msg:
                    st.error(f"⚠️ {error_msg}")
                else:
                    st.success(f"🎉 成功寫入 {count} 筆到 {target_email} 的行事曆!")
                    st.balloons()
            except FileNotFoundError as e:
                st.error(f"⚠️ {e}")
                st.info("💡 請使用本機版執行,或改天導入 Service Account 機制")
            except Exception as e:
                st.error(f"⚠️ 寫入失敗:{e}")

elif not doctor_name:
    st.warning("請輸入醫師姓名")
