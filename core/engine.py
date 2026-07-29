import asyncio
import os
import sys
import io
import time
import json
import hashlib
import difflib
import re
import sqlite3
import pathlib
import uuid
import urllib.request
import urllib.parse
from email.message import Message
from email.utils import collapse_rfc2231_value
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from core.config import config_mgr

# 强制输出为 utf-8，解决 Windows 终端 GBK 报错 (仅在有控制台时)
if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TARGET_URL = "https://wble.utar.edu.my/"
AUTH_STATE_FILE = os.path.join(os.getcwd(), "wble_auth_state.json")
SNAPSHOT_VERSION = 2
VOLATILE_QUERY_PARAMS = {
    "sesskey", "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "fbclid", "gclid", "ouid", "rtpof", "sd", "usp"
}

# ================= 工具函数 =================

async def send_wechat_notification(title, desp):
    serverchan_key = config_mgr.get("serverchan_key", "")
    if not serverchan_key:
        return
    print(f"📲 准备发送微信推送: {title}", flush=True)
    url = f"https://sctapi.ftqq.com/{serverchan_key}.send"
    data = urllib.parse.urlencode({'title': title, 'desp': desp}).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    try:
        await asyncio.to_thread(
            lambda: urllib.request.urlopen(req, timeout=20).read()
        )
        print("✅ 微信推送成功！", flush=True)
    except Exception as e:
        print(f"❌ 微信推送失败: {e}", flush=True)

def get_text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def atomic_write_text(path, text):
    temp_path = f"{path}.{os.getpid()}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def chunk_text_by_lines(text, max_chars=12000):
    chunks = []
    current_lines = []
    current_length = 0
    for line in str(text or "").splitlines():
        line_length = len(line) + 1
        if current_lines and current_length + line_length > max_chars:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_length = 0
        current_lines.append(line)
        current_length += line_length
    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks or [""]


def has_saved_wble_credentials(user_data_dir):
    """
    Check whether Chrome has a saved UTAR login without reading or decrypting
    the username/password values.
    """
    profile_dirs = [os.path.join(user_data_dir, "Default")]
    try:
        profile_dirs.extend(
            os.path.join(user_data_dir, name)
            for name in os.listdir(user_data_dir)
            if name.startswith("Profile ")
        )
    except OSError:
        pass

    for profile_dir in profile_dirs:
        for database_name in ("Login Data", "Login Data For Account"):
            database_path = os.path.join(profile_dir, database_name)
            if not os.path.isfile(database_path):
                continue
            try:
                database_uri = (
                    pathlib.Path(database_path).resolve().as_uri() + "?mode=ro"
                )
                with sqlite3.connect(database_uri, uri=True, timeout=1) as database:
                    found = database.execute(
                        """
                        SELECT 1
                        FROM logins
                        WHERE blacklisted_by_user = 0
                          AND length(password_value) > 0
                          AND (
                              instr(lower(origin_url), 'utar.edu.my') > 0
                              OR instr(lower(signon_realm), 'utar.edu.my') > 0
                          )
                        LIMIT 1
                        """
                    ).fetchone()
                if found:
                    return True
            except (OSError, sqlite3.Error):
                continue
    return False


def normalize_text(text):
    """Normalize DOM text without discarding meaningful course wording."""
    if not text:
        return ""
    text = str(text).replace("\u00a0", " ").replace("\u200b", "")
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        normalized_line = " ".join(line.split())
        if normalized_line:
            lines.append(normalized_line)
    return "\n".join(lines)


def canonicalize_url(url):
    """Remove volatile URL parts and produce a deterministic query order."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme in {"mailto", "tel"}:
            return url.strip()
        query_items = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True
            )
            if key.lower() not in VOLATILE_QUERY_PARAMS
        ]
        query_items.sort()
        return urllib.parse.urlunsplit((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            urllib.parse.urlencode(query_items, doseq=True),
            ""
        ))
    except Exception:
        return url.strip()


def get_snapshot_hash(snapshot):
    serialized = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return get_text_hash(serialized)


def snapshot_to_text(snapshot):
    """Render a structured snapshot into deterministic text for MD/AI input."""
    lines = []
    for section in snapshot.get("sections", []):
        section_id = section.get("id", "section")
        title = section.get("title", "")
        lines.append(f"【{section_id}】 {title}".strip())
        summary = section.get("summary", "")
        if summary:
            lines.append(summary)
        for activity in section.get("activities", []):
            activity_type = activity.get("type", "activity")
            text = activity.get("text", "")
            lines.append(
                f"- [{activity_type}] {activity.get('id', '')}: {text}".strip()
            )
            for link in activity.get("links", []):
                lines.append(
                    f"  - {link.get('text') or 'Link'}: {link.get('url', '')}"
                )
    external_links = snapshot.get("external_links", [])
    if external_links:
        lines.append("【重要外部链接】")
        for link in external_links:
            lines.append(f"- {link.get('text') or 'Link'}: {link.get('url', '')}")
    return "\n".join(lines)


def _flatten_activities(snapshot):
    activities = {}
    for section in snapshot.get("sections", []):
        for activity in section.get("activities", []):
            key = activity.get("id") or (
                activity.get("type"),
                activity.get("text"),
                tuple(link.get("url", "") for link in activity.get("links", []))
            )
            activities[str(key)] = {
                **activity,
                "section_id": section.get("id", "")
            }
    return activities


def diff_course_snapshots(old_snapshot, new_snapshot):
    """Return a deterministic, human-readable diff for the AI to explain."""
    changes = []
    old_activities = _flatten_activities(old_snapshot)
    new_activities = _flatten_activities(new_snapshot)

    for key in sorted(new_activities.keys() - old_activities.keys()):
        item = new_activities[key]
        changes.append(
            f"新增活动 [{item.get('type', 'activity')}] "
            f"{item.get('text', '')} ({item.get('id', key)})"
        )

    for key in sorted(old_activities.keys() - new_activities.keys()):
        item = old_activities[key]
        changes.append(
            f"移除活动 [{item.get('type', 'activity')}] "
            f"{item.get('text', '')} ({item.get('id', key)})"
        )

    for key in sorted(old_activities.keys() & new_activities.keys()):
        old_item = old_activities[key]
        new_item = new_activities[key]
        comparable_fields = ("type", "text", "links", "section_id")
        if any(old_item.get(field) != new_item.get(field) for field in comparable_fields):
            changes.append(
                f"修改活动 {new_item.get('id', key)}:\n"
                f"  旧: {old_item.get('text', '')}\n"
                f"  新: {new_item.get('text', '')}"
            )

    old_sections = {
        item.get("id", ""): item for item in old_snapshot.get("sections", [])
    }
    new_sections = {
        item.get("id", ""): item for item in new_snapshot.get("sections", [])
    }
    for section_id in sorted(new_sections.keys() - old_sections.keys()):
        section = new_sections[section_id]
        changes.append(
            f"新增章节 {section_id}: {section.get('title', '')}"
        )
    for section_id in sorted(old_sections.keys() - new_sections.keys()):
        section = old_sections[section_id]
        changes.append(
            f"移除章节 {section_id}: {section.get('title', '')}"
        )
    for section_id in sorted(old_sections.keys() & new_sections.keys()):
        old_section = old_sections[section_id]
        new_section = new_sections[section_id]
        old_header = (old_section.get("title", ""), old_section.get("summary", ""))
        new_header = (new_section.get("title", ""), new_section.get("summary", ""))
        if old_header != new_header:
            changes.append(
                f"修改章节 {section_id}:\n"
                f"  旧: {' | '.join(part for part in old_header if part)}\n"
                f"  新: {' | '.join(part for part in new_header if part)}"
            )

    old_links = {
        (item.get("text", ""), item.get("url", ""))
        for item in old_snapshot.get("external_links", [])
    }
    new_links = {
        (item.get("text", ""), item.get("url", ""))
        for item in new_snapshot.get("external_links", [])
    }
    for text, url in sorted(new_links - old_links):
        changes.append(f"新增外部链接: {text or 'Link'} — {url}")
    for text, url in sorted(old_links - new_links):
        changes.append(f"移除外部链接: {text or 'Link'} — {url}")

    if not changes:
        old_lines = snapshot_to_text(old_snapshot).splitlines()
        new_lines = snapshot_to_text(new_snapshot).splitlines()
        fallback_diff = list(difflib.unified_diff(
            old_lines, new_lines, fromfile="旧快照", tofile="新快照", lineterm=""
        ))
        if fallback_diff:
            changes.append("结构顺序或其他内容发生变化:\n" + "\n".join(fallback_diff[:80]))

    return "\n".join(changes)


def is_valid_course(title: str) -> bool:
    title = title.strip()
    if not re.search(r'\b[A-Z]{3,5}\s*-?\s*\d{4,5}\b', title):
        return False
        
    # Check against user's blacklisted courses
    blacklist = config_mgr.get("blacklisted_courses", [])
    for black_item in blacklist:
        if black_item in title:
            return False
            
    # Check explicitly deleted/ignored courses (can be added to blacklist)
    return True

# ================= 核心业务逻辑 =================

async def extract_course_links(page):
    print("🔍 正在提取所有专业课链接...", flush=True)
    course_elements = await page.locator("a[href*='course/view.php']").all()
    courses = {}
    for el in course_elements:
        title = await el.inner_text()
        href = await el.get_attribute("href")
        title = title.strip().replace("\n", " ")
        if href and is_valid_course(title) and href not in courses.values():
            courses[title] = href
    print(f"✅ 成功筛选出 {len(courses)} 门需要追踪的课程！", flush=True)
    return courses

async def invoke_llm_with_fallback(prompt_template, kwargs_dict):
    """
    大模型高可用轮询系统 (Fallback)
    优先级: 1. Azure GPT-4o -> 2. Groq -> 3. Gemini -> 4. Kimi
    """
    # 1. GitHub Models
    try:
        github_key = config_mgr.get("api_keys", {}).get("openai", "")
        if github_key:
            print("   🤖 [AI 引擎] 尝试主引擎: GitHub Models GPT-4o (User Token)...", flush=True)
            llm = ChatOpenAI(model="gpt-4o", api_key=github_key, base_url="https://models.inference.ai.azure.com", temperature=0.1)
            chain = prompt_template | llm
            res = await chain.ainvoke(kwargs_dict)
            return res.content
            print("   🤖 [AI 引擎] 尝试主引擎: GitHub Models GPT-4.1...", flush=True)
            llm = ChatOpenAI(
                model="openai/gpt-4.1",
                api_key=github_key,
                base_url="https://models.github.ai/inference",
                default_headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                temperature=0.1,
                timeout=45,
                max_retries=1,
            )
        else:
            raise ValueError("未配置 GitHub Token，自动回退到下一个可用引擎")
    except Exception as e:
        print(f"      ⚠️ GitHub Models 调用失败: {type(e).__name__}", flush=True)

    # 2. Groq (免费高速引擎)
    try:
        groq_key = config_mgr.get("api_keys", {}).get("groq", "")
        if groq_key:
            print("   🤖 [AI 引擎] 启动备用引擎: Groq (Llama 3)...", flush=True)
            llm = ChatOpenAI(
                model="llama-3.3-70b-versatile",
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
                temperature=0.1,
                timeout=45,
                max_retries=1,
            )
            chain = prompt_template | llm
            res = await chain.ainvoke(kwargs_dict)
            return res.content
        else:
            raise ValueError("未配置 Groq Token")
    except Exception as e:
        print(f"      ⚠️ Groq 引擎调用失败: {type(e).__name__}", flush=True)

    # 3. Gemini (备用引擎，不花钱)
    try:
        gemini_key = config_mgr.get("api_keys", {}).get("gemini", "")
        if gemini_key:
            print("   🤖 [AI 引擎] 启动备用引擎: Gemini 2.0 Flash...", flush=True)
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", google_api_key=gemini_key, temperature=0.1)
            chain = prompt_template | llm
            res = await chain.ainvoke(kwargs_dict)
            return res.content
        else:
            raise ValueError("未配置 Gemini Token")
        if not gemini_key:
            raise ValueError("未配置 Gemini API Key")
        print("   🤖 [AI 引擎] 启动备用引擎: Gemini 3.5 Flash...", flush=True)
        from google import genai
        client = genai.Client(api_key=gemini_key)
        
        prompt_text = prompt_template.format(**kwargs_dict)
        interaction = await asyncio.to_thread(
            client.interactions.create,
            model="gemini-3.5-flash",
            input=prompt_text,
        )
        return interaction.output_text
    except ImportError:
        print("      ⚠️ 未安装 langchain-google-genai，无法调用 Gemini。", flush=True)
    except Exception as e:
        print(f"      ⚠️ Gemini 备用引擎调用失败: {type(e).__name__} - {e}", flush=True)

    # 4. Kimi (最终兜底防线，需扣费)
    try:
        kimi_key = config_mgr.get("api_keys", {}).get("kimi", "")
        if kimi_key:
            print("   🤖 [AI 引擎] 启动最终兜底防线: Kimi (Moonshot)...", flush=True)
            llm = ChatOpenAI(
                model="moonshot-v1-8k", 
                base_url="https://api.moonshot.cn/v1", 
                api_key=kimi_key,
                temperature=1.0
            )
            chain = prompt_template | llm
            res = await chain.ainvoke(kwargs_dict)
            return res.content
        else:
            raise ValueError("未配置 Kimi Token")
        if not kimi_key:
            raise ValueError("未配置 Kimi API Key")
        llm = ChatOpenAI(
            model="kimi-k2.6", 
            base_url="https://api.moonshot.cn/v1", 
            api_key=kimi_key,
            temperature=1.0,
            timeout=45,
            max_retries=1,
        )
        chain = prompt_template | llm
        res = await chain.ainvoke(kwargs_dict)
        return res.content
    except Exception as e:
        print(f"      ⚠️ Kimi 调用失败: {type(e).__name__}", flush=True)
        
    raise Exception("所有大模型引擎均已阵亡或未配置，请补充额度或设置 API Key。")


async def categorize_file_with_ai(filename, link_text):
    """当正则规则无法判断时，召唤大模型进行智能兜底分类"""
    prompt = PromptTemplate.from_template(
        "你是一个聪明的大学课件整理专家。\n"
        "我有一个文件无法通过简单的规则分类，请你根据它的【文件名】和它在网页上的【超链接文字】进行推理，判断它应该放进哪个文件夹。\n\n"
        "【文件信息】\n"
        "文件名: {filename}\n"
        "网页显示文字: {link_text}\n\n"
        "请务必从以下几个标准文件夹中选择最合适的一个（只输出文件夹的英文原名，不要输出任何标点或额外解释）：\n"
        "1. Lectures (理论课件、讲义、幻灯片)\n"
        "2. Practicals_and_Tutorials (实验课、辅导课、代码、练习题)\n"
        "3. Course_Information (课程大纲、教学计划、要求)\n"
        "4. Assignments_and_Projects (作业题、Project材料)\n"
        "5. Assessments_and_Exams (考卷、测验、复习资料)\n"
        "6. Others (确实无法判断的杂项文件)\n"
    )
    try:
        result_content = await invoke_llm_with_fallback(prompt, {"filename": filename, "link_text": link_text})
        category = result_content.strip()
        valid = ["Lectures", "Practicals_and_Tutorials", "Course_Information", "Assignments_and_Projects", "Assessments_and_Exams"]
        for v in valid:
            if v in category:
                return v, True
        return "Others", True  # AI 回复了但确实是 Others
    except Exception:
        return "Others", False  # AI 调用失败，标记为未成功


async def generate_md_archive(course_name, text_content, course_dir):
    print(f"   📝 正在让大模型提炼【{course_name}】的极致精简笔记...", flush=True)
    try:
        chunks = chunk_text_by_lines(text_content)
        if len(chunks) == 1:
            merged_source = chunks[0]
        else:
            extracted_notes = []
            extraction_prompt = PromptTemplate.from_template(
                "你是课程信息提取器。请从以下课程内容分块中完整提取事实，"
                "尤其不要遗漏成绩权重、日期、Deadline、考试、作业、公告和链接。"
                "只写原文明确存在的内容，不要猜测。\n\n"
                "【课程】{course_name}\n"
                "【分块 {chunk_number}/{chunk_count}】\n{web_content}"
            )
            for index, chunk in enumerate(chunks, start=1):
                extracted_notes.append(
                    await invoke_llm_with_fallback(
                        extraction_prompt,
                        {
                            "course_name": course_name,
                            "chunk_number": index,
                            "chunk_count": len(chunks),
                            "web_content": chunk,
                        },
                    )
                )
            merged_source = "\n\n--- 分块 ---\n\n".join(extracted_notes)

        prompt = PromptTemplate.from_template(
            "你是一个极其干练的大学课业整理专家。下面是课程开学至今的网页原始内容（可能包含提取的链接）。\n"
            "请帮我提炼出最核心的信息，严禁照抄大段原文。必须遵循以下排版：\n"
            "### ⚖️ 成绩评估权重\n"
            "(如果有提到 Assignment, Midterm 占多少分，列成清晰的无序列表。如果没有直接写“暂无”)\n"
            "### 📅 重要截止日期\n"
            "(提取所有提到的 deadline 和考试时间，重点加粗)\n"
            "### 📢 核心公告提要\n"
            "(把长篇大论的公告压缩成一两句话的要点)\n"
            "### 🔗 重要在线链接\n"
            "(把下方提供的 Teams会议、Google表格等外部链接用 Markdown 语法列出。如果遇到后缀为 '#' 的空链接，请在链接旁备注 '*(⚠️ 链接可能出错或需弹窗，请前往 Moodle 原网页手动处理)*')\n\n"
            "以下内容是逐块提取结果。请合并重复项，但不要删除任何明确日期、"
            "权重、作业或链接。\n\n【逐块提取结果】:\n{web_content}"
        )
        result_content = await invoke_llm_with_fallback(
            prompt,
            {"web_content": merged_source},
        )
        
        md_file_path = os.path.join(course_dir, "课程重点归档.md")
        archive_text = (
            f"# {course_name}\n"
            f"*归档时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n"
            f"{result_content}"
        )
        atomic_write_text(md_file_path, archive_text)
        
        return True  # 成功
    except Exception:
        print(f"   ⚠️ 笔记生成跳过 (所有大模型引擎均失败或无额度)。", flush=True)
        return False  # 失败，需要下次重试

def normalize_ics_calendar(ics_content):
    match = re.search(
        r"(BEGIN:VCALENDAR.*?END:VCALENDAR)",
        ics_content,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None

    calendar_text = match.group(1).strip().replace("\r\n", "\n")
    events = re.findall(
        r"BEGIN:VEVENT.*?END:VEVENT",
        calendar_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not events:
        return None

    normalized_events = []
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for event in events:
        lines = [line.rstrip() for line in event.splitlines() if line.strip()]
        start_lines = [
            line for line in lines if line.upper().startswith("DTSTART")
        ]
        if not start_lines:
            return None
        for start_line in start_lines:
            upper_line = start_line.upper()
            if (
                "VALUE=DATE" not in upper_line
                and "TZID=ASIA/KUALA_LUMPUR" not in upper_line
            ):
                return None

        if not any(line.upper().startswith("UID:") for line in lines):
            event_hash = hashlib.sha256(
                "\n".join(lines).encode("utf-8")
            ).hexdigest()[:20]
            lines.insert(-1, f"UID:{event_hash}@wble-agent")
        if not any(line.upper().startswith("DTSTAMP:") for line in lines):
            lines.insert(-1, f"DTSTAMP:{timestamp}")
        normalized_events.append("\n".join(lines))

    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//UTAR WBLE Agent//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-TIMEZONE:Asia/Kuala_Lumpur",
    ]
    return "\r\n".join(
        header + normalized_events + ["END:VCALENDAR", ""]
    )


async def generate_ics_calendar(course_name, course_dir):
    md_file_path = os.path.join(course_dir, "课程重点归档.md")
    if not os.path.exists(md_file_path):
        return False  # 必须先有 MD 才能生成 ICS
        
    with open(md_file_path, "r", encoding="utf-8") as f:
        result_content = f.read()
        
    print(f"   📅 正在提取【{course_name}】的日程安排并生成日历...", flush=True)
    ics_prompt = PromptTemplate.from_template(
        "你是一个严格的日历生成器。请阅读以下已经为你精简好的【课程重点笔记】，提取其中所有需要学生【去参加/去完成】的有明确日期时间的事件。\n\n"
        "【包含类型】:\n"
        "1. 所有的作业截止日期 (Deadline)\n"
        "2. 考试 (Exams) & 测验 (Quiz & Test)\n"
        "3. 补课 (Replacement Classes) 或 加课 (Additional Classes) - 请具体写明具体时间和教室 (如有)\n\n"
        "【禁止包含类型】:\n"
        "1. 纯粹的「课堂取消/停课」(Canceled Class) - 除非伴随有明确的补课时间安排（此时只保留并写入补课日程）\n\n"
        "如果没有找到任何符合「包含类型」的明确日期事件，请直接回复：NONE\n\n"
        "如果找到了，请务必直接输出标准 iCalendar (.ics) 格式的内容，不需要任何额外解释。请参照以下模板：\n"
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//WBLE Agent//EN\n"
        "BEGIN:VEVENT\nSUMMARY:Assignment 1\n"
        "DTSTART;TZID=Asia/Kuala_Lumpur:20260810T150000\n"
        "DTEND;TZID=Asia/Kuala_Lumpur:20260810T160000\nEND:VEVENT\n"
        "END:VCALENDAR\n\n"
        "注意：所有带时间的事件必须使用 TZID=Asia/Kuala_Lumpur，"
        "绝对不要使用结尾为 Z 的 UTC 时间。如果只有日期没有具体时间，"
        "使用 DTSTART;VALUE=DATE:YYYYMMDD。必须输出标准 ICS。\n\n"
        "【课程重点笔记】:\n{web_content}"
    )
    try:
        ics_content = await invoke_llm_with_fallback(ics_prompt, {"web_content": result_content})
        ics_content = ics_content.strip()
        
        ics_path = os.path.join(course_dir, "Reminder.ics")

        if re.fullmatch(r"NONE[.!]?", ics_content, re.IGNORECASE):
            print("   ℹ️ 检查完毕，当前无明确日程，不需要日历文件。", flush=True)
            if os.path.exists(ics_path):
                os.remove(ics_path)
                print("   🗑️ 已自动清理过期的 Reminder.ics。", flush=True)
            return True

        normalized_calendar = normalize_ics_calendar(ics_content)
        if normalized_calendar:
            atomic_write_text(ics_path, normalized_calendar)
            print("   ✅ 日历文件 Reminder.ics 生成/更新成功！", flush=True)
            return True

        print(
            "   ⚠️ AI 返回的日历缺少马来西亚时区或格式无效；"
            "已保留旧 Reminder.ics，稍后重试。",
            flush=True,
        )
        return False
    except Exception as e:
        print(f"   ⚠️ 日历生成失败，稍后自动重试。({e})", flush=True)
        return False


def smart_categorize_local(filename, link_text):
    """
    本地急速规则引擎：替代缓慢的大模型，通过正则表达式瞬间完成分类。
    逻辑：综合考虑文件名和网页链接的文字。
    """
    text = f"{filename} {link_text}".lower()
    
    # 1. 课程信息类
    if re.search(r'course\s*info|syllabus|plan|venue|schedule', text):
        return "Course_Information"
        
    # 2. 实验/辅导课类 (匹配 P01, T01, tutorial, practical, lab, answer)
    # \b 确保匹配的是独立的词组，(?:\b|_) 允许紧跟下划线，例如 P07_
    if re.search(r'\b[pt]\d{1,2}(?:\b|_)|tutorial|practical|lab|ans', text):
        return "Practicals_and_Tutorials"
        
    # 3. 理论课类 (匹配 L01, lecture, slide, chapter, note)
    if re.search(r'\bl\d{1,2}(?:\b|_)|lecture|slide|chapter|topic|note', text):
        return "Lectures"
        
    # 4. 考试测验类
    if re.search(r'exam|test|past\s*year|pyq|question|midterm|final', text):
        return "Assessments_and_Exams"
        
    # 5. 作业项目类
    if re.search(r'assignment|project|coursework', text):
        return "Assignments_and_Projects"
        
    # 6. 后备推断：如果没有明显的关键词，但如果是代码/网页文件，大概率是实验课或练习
    ext = filename.split('.')[-1].lower()
    if ext in ['ipynb', 'py', 'java', 'cpp', 'c', 'html', 'js', 'css']:
        return "Practicals_and_Tutorials"
        
    return None


class FileTooLargeError(Exception):
    pass


def safe_download_filename(filename, fallback="downloaded_file"):
    filename = urllib.parse.unquote(str(filename or ""))
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    filename = filename.rstrip(" .")
    if not filename or filename in {".", ".."}:
        filename = fallback

    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    stem = filename.split(".", 1)[0].upper()
    if stem in reserved:
        filename = f"_{filename}"
    return filename[:240]


def filename_from_response(final_url, content_disposition):
    if content_disposition:
        message = Message()
        message["content-disposition"] = content_disposition
        candidate = message.get_filename()
        if isinstance(candidate, tuple):
            candidate = collapse_rfc2231_value(candidate)
        if candidate:
            return safe_download_filename(candidate)

    url_name = urllib.parse.urlsplit(final_url).path.rsplit("/", 1)[-1]
    return safe_download_filename(
        url_name,
        fallback=f"downloaded_{int(time.time())}.bin",
    )


def _fetch_url_to_temp(
    url,
    temp_dir,
    max_size_bytes,
    cookie_header,
    user_agent,
    allow_html,
):
    headers = {"User-Agent": user_agent}
    if cookie_header:
        headers["Cookie"] = cookie_header
    request = urllib.request.Request(url, headers=headers)
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.part")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            declared_size = response.headers.get("Content-Length")
            if declared_size:
                try:
                    if int(declared_size) > max_size_bytes:
                        raise FileTooLargeError(
                            f"{int(declared_size) / 1024 / 1024:.1f} MB"
                        )
                except ValueError:
                    pass

            if allow_html and content_type in {
                "text/html", "application/xhtml+xml"
            }:
                html_limit = min(max_size_bytes, 5 * 1024 * 1024)
                body = response.read(html_limit + 1)
                if len(body) > html_limit:
                    raise FileTooLargeError("HTML preview exceeds 5 MB")
                charset = response.headers.get_content_charset() or "utf-8"
                return {
                    "kind": "html",
                    "final_url": final_url,
                    "text": body.decode(charset, errors="replace"),
                }

            total_size = 0
            with open(temp_path, "wb") as temp_file:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > max_size_bytes:
                        raise FileTooLargeError(
                            f"download exceeded "
                            f"{max_size_bytes / 1024 / 1024:.0f} MB"
                        )
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            return {
                "kind": "file",
                "final_url": final_url,
                "filename": filename_from_response(
                    final_url,
                    response.headers.get("Content-Disposition", ""),
                ),
                "temp_path": temp_path,
                "size": total_size,
            }
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


async def fetch_url_to_temp(page, url, temp_dir, max_size_bytes, allow_html):
    cookies = await page.context.cookies(url)
    cookie_header = "; ".join(
        f"{cookie['name']}={cookie['value']}" for cookie in cookies
    )
    try:
        user_agent = await page.evaluate("() => navigator.userAgent")
    except Exception:
        user_agent = "Mozilla/5.0 UTAR-WBLE-Agent"
    return await asyncio.to_thread(
        _fetch_url_to_temp,
        url,
        temp_dir,
        max_size_bytes,
        cookie_header,
        user_agent,
        allow_html,
    )


async def place_downloaded_file(result, files_dir, link_text):
    filename = safe_download_filename(result.get("filename"))
    temp_path = result["temp_path"]
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension in {"mp4", "mkv", "avi", "mov"}:
        os.remove(temp_path)
        print(f"        ⚠️ 按策略跳过视频: {filename}", flush=True)
        return True, 0, True

    category = smart_categorize_local(filename, link_text)
    if not category:
        print(
            f"        🧠 正则匹配失败，召唤 AI 兜底推理 [{filename}]...",
            flush=True,
        )
        category, ai_ok = await categorize_file_with_ai(filename, link_text)
    else:
        ai_ok = True
        print(
            f"        ⚡ 本地急速分类 [{filename}] -> {category}",
            flush=True,
        )

    section_dir = os.path.join(files_dir, category)
    os.makedirs(section_dir, exist_ok=True)
    save_path = os.path.abspath(os.path.join(section_dir, filename))
    section_root = os.path.abspath(section_dir)
    if os.path.commonpath([section_root, save_path]) != section_root:
        os.remove(temp_path)
        raise ValueError(f"Unsafe download filename: {filename}")

    if os.path.exists(save_path):
        os.remove(temp_path)
        print(
            f"        ⏭️ 硬盘上已存在，跳过下载: {category}/{filename}",
            flush=True,
        )
        return True, 0, ai_ok

    os.replace(temp_path, save_path)
    print(f"        ✅ 下载完成: {category}/{filename}", flush=True)
    return True, 1, ai_ok


async def download_from_current_page(page, course_dir, state_db, course_name):
    files_dir = os.path.join(course_dir, "Files")
    os.makedirs(files_dir, exist_ok=True)
    temp_dir = os.path.join(files_dir, ".partial")
    max_size_bytes = max(
        1, int(config_mgr.get("max_file_size_mb", 50))
    ) * 1024 * 1024

    links_count = await page.locator("a[href*='mod/resource/view.php']").count()
    new_files_count = 0

    for i in range(links_count):
        link_el = page.locator("a[href*='mod/resource/view.php']").nth(i)
        href = await link_el.evaluate("node => node.href")
        resource_key = canonicalize_url(href)
        link_text = (await link_el.inner_text()).strip().replace("\n", " ")

        if not resource_key:
            continue
        if resource_key in state_db[course_name]["downloaded_files"]:
            print(
                f"      ⏭️ 数据库中已有完成记录: {link_text[:40]}",
                flush=True,
            )
            continue

        print(
            f"      ⬇️ [发现目标] 流式下载检查... ({link_text[:30]})",
            flush=True,
        )
        resource_complete = True
        try:
            result = await fetch_url_to_temp(
                page, href, temp_dir, max_size_bytes, allow_html=True
            )
            if result["kind"] == "file":
                placed_ok, added, classification_ok = await place_downloaded_file(
                    result, files_dir, link_text
                )
                resource_complete = placed_ok
                new_files_count += added
                if not classification_ok:
                    state_db[course_name]["has_unclassified_files"] = True
            else:
                soup = BeautifulSoup(result["text"], "html.parser")
                file_urls = {
                    urllib.parse.urljoin(result["final_url"], link.get("href"))
                    for link in soup.select("a[href]")
                    if "file.php/" in (link.get("href") or "")
                }
                if not file_urls:
                    print(
                        "        ⚠️ 预览页未找到 file.php 文件，保留待重试。",
                        flush=True,
                    )
                    resource_complete = False

                for file_url in sorted(file_urls):
                    try:
                        file_result = await fetch_url_to_temp(
                            page,
                            file_url,
                            temp_dir,
                            max_size_bytes,
                            allow_html=False,
                        )
                        placed_ok, added, classification_ok = (
                            await place_downloaded_file(
                            file_result, files_dir, link_text
                            )
                        )
                        resource_complete = resource_complete and placed_ok
                        new_files_count += added
                        if not classification_ok:
                            state_db[course_name][
                                "has_unclassified_files"
                            ] = True
                    except FileTooLargeError as error:
                        resource_complete = False
                        print(
                            f"        ⚠️ 文件超过限制 ({error})，"
                            "本资源将在以后继续检查。",
                            flush=True,
                        )
                    except Exception as error:
                        resource_complete = False
                        print(f"        ⚠️ 文件下载失败: {error}", flush=True)
        except FileTooLargeError as error:
            resource_complete = False
            print(
                f"        ⚠️ 文件超过限制 ({error})，"
                "提高限制后会自动重试。",
                flush=True,
            )
        except Exception as error:
            resource_complete = False
            print(
                f"      ⚠️ 下载失败，保留待重试: {error}",
                flush=True,
            )

        if resource_complete:
            state_db[course_name]["downloaded_files"].append(resource_key)

    if os.path.isdir(temp_dir) and not os.listdir(temp_dir):
        os.rmdir(temp_dir)
    return new_files_count

def _normalize_link_list(items):
    normalized_items = {}
    for item in items or []:
        text = normalize_text(item.get("text", ""))
        url = canonicalize_url(item.get("url", ""))
        if not url:
            continue
        normalized_items[(text, url)] = {"text": text, "url": url}
    return [
        normalized_items[key]
        for key in sorted(normalized_items, key=lambda value: (value[1], value[0]))
    ]


async def extract_course_snapshot(page, course_name):
    """
    Extract the stable old-Moodle course structure seen in UTAR WBLE.
    Never falls back to .course-content because it wraps all three columns.
    """
    try:
        raw_snapshot = await page.evaluate('''() => {
            const root = document.querySelector("#middle-column");
            if (!root) {
                return {ok: false, reason: "missing #middle-column"};
            }

            const cleanText = (node) => {
                if (!node) return "";
                const clone = node.cloneNode(true);
                clone.querySelectorAll(
                    "script, style, form, .side, .accesshide"
                ).forEach(el => el.remove());
                return clone.innerText || clone.textContent || "";
            };

            const knownTypes = [
                "resource", "label", "forum", "assignment", "assign",
                "folder", "page", "quiz", "url"
            ];
            const rows = Array.from(new Set(
                Array.from(root.querySelectorAll(
                    "table.weeks tr.section[id^='section-'], "
                    + "table.topics tr.section[id^='section-'], "
                    + "ul.weeks li.section[id^='section-'], "
                    + "ul.topics li.section[id^='section-'], "
                    + "li.section.main[id^='section-']"
                ))
            ));
            const sections = rows.map(row => {
                const content = row.querySelector("td.content, .content");
                const activities = content
                    ? Array.from(
                        content.querySelectorAll(
                            "li.activity[id^='module-']"
                        )
                    ).map(item => {
                        const classes = Array.from(item.classList);
                        const type = knownTypes.find(name => classes.includes(name))
                            || classes.find(name => name !== "activity")
                            || "activity";
                        const links = Array.from(
                            item.querySelectorAll("a[href]")
                        ).map(link => ({
                            text: cleanText(link).trim() || "Link",
                            url: link.href
                        }));
                        return {
                            id: item.id,
                            type,
                            text: cleanText(item),
                            links
                        };
                    })
                    : [];
                return {
                    id: row.id,
                    title: cleanText(
                        row.querySelector(
                            ".weekdates, .sectionname, h3.sectionname"
                        )
                    ),
                    summary: cleanText(
                        content ? content.querySelector(".summary") : null
                    ),
                    activities
                };
            });

            const keywords = [
                "teams.microsoft", "docs.google", "drive.google", "zoom.us",
                "webex", "meet.google", "chat.whatsapp"
            ];
            const externalLinks = Array.from(root.querySelectorAll("a[href]"))
                .filter(link => keywords.some(key => link.href.includes(key)))
                .map(link => ({
                    text: cleanText(link).trim() || "Link",
                    url: link.href
                }));

            return {
                ok: rows.length > 0,
                reason: rows.length > 0
                    ? ""
                    : "missing weeks/topics section rows",
                sections,
                external_links: externalLinks
            };
        }''')
    except Exception as e:
        return None, f"DOM extraction error: {e}"

    if not raw_snapshot or not raw_snapshot.get("ok"):
        reason = (raw_snapshot or {}).get("reason", "empty extraction result")
        return None, reason

    sections = []
    for section in raw_snapshot.get("sections", []):
        activities = []
        for activity in section.get("activities", []):
            activities.append({
                "id": normalize_text(activity.get("id", "")),
                "type": normalize_text(activity.get("type", "")) or "activity",
                "text": normalize_text(activity.get("text", "")),
                "links": _normalize_link_list(activity.get("links", []))
            })
        sections.append({
            "id": normalize_text(section.get("id", "")),
            "title": normalize_text(section.get("title", "")),
            "summary": normalize_text(section.get("summary", "")),
            "activities": activities
        })

    snapshot = {
        "version": SNAPSHOT_VERSION,
        "course": course_name,
        "sections": sections,
        "external_links": _normalize_link_list(
            raw_snapshot.get("external_links", [])
        )
    }
    activity_count = sum(
        len(section.get("activities", [])) for section in sections
    )
    if not sections:
        return None, (
            f"implausible course structure: sections={len(sections)}, "
            f"activities={activity_count}"
        )
    return snapshot, ""


async def deep_scan_course(page, course_link, course_dir, state_db, course_name):
    """Scan the course homepage and linked content pages."""
    await page.goto(course_link, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    snapshot, extraction_error = await extract_course_snapshot(page, course_name)
    extraction_ok = snapshot is not None
    text_content = snapshot_to_text(snapshot) if extraction_ok else ""
    if extraction_ok:
        activity_count = sum(
            len(section.get("activities", []))
            for section in snapshot.get("sections", [])
        )
        print(
            f"      🧭 结构化提取成功: "
            f"{len(snapshot.get('sections', []))} 个章节, "
            f"{activity_count} 个活动。",
            flush=True
        )
    else:
        print(
            f"      ⚠️ 内容提取失败 ({extraction_error})。"
            "本轮不会覆盖旧快照或触发文字更新。",
            flush=True
        )

    total_new_files = await download_from_current_page(
        page, course_dir, state_db, course_name
    )

    # Include content-bearing activity pages. Their resource links are downloaded,
    # while the course-page module inventory remains the deterministic snapshot.
    subpage_selector = ", ".join([
        "a[href*='mod/folder/view.php']",
        "a[href*='mod/page/view.php']",
        "a[href*='mod/forum/view.php']",
        "a[href*='mod/assignment/view.php']",
        "a[href*='mod/assign/view.php']"
    ])
    sub_links_locators = await page.locator(subpage_selector).all()
    sub_hrefs = []
    for element in sub_links_locators:
        href = canonicalize_url(await element.get_attribute("href"))
        if href and href not in sub_hrefs:
            sub_hrefs.append(href)

    for sub_href in sub_hrefs:
        print(
            f"      🤿 正在深潜进入子网页: {sub_href.split('id=')[-1]}",
            flush=True
        )
        try:
            await page.goto(sub_href, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            total_new_files += await download_from_current_page(
                page, course_dir, state_db, course_name
            )
        except Exception:
            print("      ⚠️ 深潜失败，跳过该子页面", flush=True)

    return total_new_files, text_content, snapshot, extraction_ok


async def analyze_course_updates(course_name, diff_text, new_text):
    try:
        prompt = PromptTemplate.from_template(
            "你正在分析课程【{course_name}】一次已经由程序精确确认的网页变化。\n"
            "下面的【确定性差异】来自旧、新结构化快照，不要猜测差异之外的内容。\n"
            "请用极其精简的一句话或列表说明老师新增、修改或移除了什么；"
            "优先指出公告、截止日期、作业和课件。\n\n"
            "【确定性差异】\n{diff_text}\n\n"
            "【最新课程内容（仅作语境参考）】\n{new_text}"
        )
        result_content = await invoke_llm_with_fallback(prompt, {
            "course_name": course_name,
            "diff_text": diff_text[:6000],
            "new_text": new_text[:6000]
        })
        return result_content
    except Exception as e:
        print(f"   ⚠️ 大模型分析更新失败 (所有引擎均失败或无额度): {e}", flush=True)
        deterministic_summary = diff_text[:1200] or "课程结构发生变化。"
        return f"已确认网页更新：\n{deterministic_summary}"

class WBLEScanner:
    def __init__(self):
        self.playwright = None
        self.context = None
        self.page = None
        self.has_saved_wble_credentials = False
        
    async def init_browser(self, is_background=False):
        print(f"🤖 初始化浏览器引擎 (后台模式={is_background})...", flush=True)
        user_data_dir = os.path.join(os.getcwd(), "chrome_data")
        self.has_saved_wble_credentials = has_saved_wble_credentials(
            user_data_dir
        )
        if self.has_saved_wble_credentials:
            print(
                "🔐 当前 Chrome profile 已保存 WBLE 登录凭据。",
                flush=True
            )
        self.playwright = await async_playwright().start()
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir,
                headless=is_background,
                channel="chrome",
                ignore_https_errors=True
            )
        except Exception as chrome_error:
            print(
                "⚠️ 系统 Chrome 启动失败，尝试已安装的 Playwright Chromium。",
                flush=True,
            )
            try:
                self.context = (
                    await self.playwright.chromium.launch_persistent_context(
                        user_data_dir,
                        headless=is_background,
                        ignore_https_errors=True,
                    )
                )
            except Exception as chromium_error:
                await self.playwright.stop()
                self.playwright = None
                raise RuntimeError(
                    "无法启动浏览器。请安装或更新 Google Chrome，并确认没有"
                    "另一个 WBLE Agent 正在占用 chrome_data。"
                ) from chromium_error

        if is_background and os.path.exists(AUTH_STATE_FILE):
            try:
                with open(AUTH_STATE_FILE, "r", encoding="utf-8") as auth_file:
                    auth_state = json.load(auth_file)
                cookies = auth_state.get("cookies", [])
                if cookies:
                    await self.context.add_cookies(cookies)
                    print(f"🔐 已载入 {len(cookies)} 个已保存的登录 Cookie。", flush=True)
            except Exception as e:
                print(f"⚠️ 无法载入已保存的登录状态: {e}", flush=True)

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def wait_for_password_save_confirmation(self, page):
        """
        Pause after an interactive login so Chrome's password prompt is not
        interrupted by the scanner immediately navigating away.
        """
        overlay_id = "wble-agent-login-confirmation"
        try:
            await page.evaluate(
                '''overlayId => {
                    document.getElementById(overlayId)?.remove();

                    const overlay = document.createElement("div");
                    overlay.id = overlayId;
                    overlay.style.cssText = [
                        "position:fixed",
                        "inset:0",
                        "z-index:2147483647",
                        "display:flex",
                        "align-items:center",
                        "justify-content:center",
                        "background:rgba(15,23,42,.46)",
                        "font-family:'Segoe UI',Arial,sans-serif"
                    ].join(";");

                    const panel = document.createElement("div");
                    panel.style.cssText = [
                        "width:min(460px,calc(100vw - 40px))",
                        "padding:28px",
                        "border-radius:20px",
                        "background:#fff",
                        "box-shadow:0 24px 70px rgba(15,23,42,.28)",
                        "color:#172033",
                        "text-align:center"
                    ].join(";");

                    const title = document.createElement("div");
                    title.textContent = "登录成功";
                    title.style.cssText =
                        "font-size:24px;font-weight:700;margin-bottom:12px";

                    const message = document.createElement("div");
                    message.textContent =
                        "请先处理 Chrome 右上角的“保存密码”提示。完成后点击下方按钮，WBLE Agent 才会开始扫描。";
                    message.style.cssText =
                        "font-size:15px;line-height:1.65;color:#526078;margin-bottom:22px";

                    const button = document.createElement("button");
                    button.type = "button";
                    button.textContent = "我已处理，开始扫描";
                    button.style.cssText = [
                        "border:0",
                        "border-radius:12px",
                        "padding:12px 24px",
                        "background:#16a34a",
                        "color:#fff",
                        "font-size:15px",
                        "font-weight:700",
                        "cursor:pointer"
                    ].join(";");
                    button.addEventListener("click", () => overlay.remove());

                    panel.append(title, message, button);
                    overlay.append(panel);
                    document.body.append(overlay);
                }''',
                overlay_id
            )
            print(
                "🔑 登录成功。扫描已暂停，请先处理 Chrome 的保存密码提示，"
                "再点击网页中的“我已处理，开始扫描”。",
                flush=True
            )
            await page.wait_for_function(
                "overlayId => !document.getElementById(overlayId)",
                arg=overlay_id,
                timeout=120000
            )
            print("▶️ 用户已确认，继续执行扫描。", flush=True)
        except Exception as error:
            print(
                f"⚠️ 登录确认提示未完成 ({type(error).__name__})，"
                "等待结束后继续扫描。",
                flush=True
            )
            try:
                await page.locator(f"#{overlay_id}").evaluate(
                    "element => element.remove()"
                )
            except Exception:
                pass

    async def wait_for_login(self, is_background=False):
        manual_login_page_seen = False
        if is_background:
            # 后台没有用户可以选择校区，因此必须直接进入上次登录成功后
            # 保存下来的校区主页，不能从 WBLE 公共入口开始。
            login_check_url = config_mgr.get("dashboard_url", TARGET_URL)
            print(f"👻 正在后台验证已保存的 WBLE 会话: {login_check_url}", flush=True)
        else:
            # Once a campus has been selected, go there directly. This lets the
            # persistent Chrome profile reuse its cookies and saved credentials.
            login_check_url = config_mgr.get("dashboard_url", "") or TARGET_URL
            print("\n" + "❗"*25, flush=True)
            print("🛑 登录状态检查：", flush=True)
            if login_check_url == TARGET_URL:
                print("👉 请在浏览器里选择校区并完成登录。", flush=True)
                print(
                    "👉 ⚠️ 选择校区后弹出新标签页属于正常现象。",
                    flush=True
                )
            else:
                print(
                    f"👉 正在直接打开上次使用的校区: {login_check_url}",
                    flush=True
                )
            print(
                "👉 如出现登录页，请完成登录；程序会等待你处理保存密码提示。",
                flush=True
            )
            print("❗"*25 + "\n", flush=True)

        await self.page.goto(login_check_url, wait_until="domcontentloaded")

        # 后台给网络和页面脚本 15 秒完成跳转与渲染；手动模式仍允许
        # 用户在 10 分钟内完成校区选择及身份验证。
        max_retries = 15 if is_background else 600

        for _ in range(max_retries):
            # 关键修复：监控 context 里所有标签页，而不只是初始页
            all_pages = self.context.pages
            for p in all_pages:
                current_url = p.url
                if not is_background and "login" in current_url.lower():
                    manual_login_page_seen = True
                if ("wble" in current_url and ".utar.edu.my" in current_url 
                        and "login" not in current_url 
                        and current_url.rstrip("/") != "https://wble.utar.edu.my"):
                    try:
                        logout_count = await p.locator("a[href*='logout.php']").count()
                        course_count = await p.locator("a[href*='course/view.php']").count()
                        if logout_count > 0 or course_count > 0:
                            print("\n✅ 已自动检测到登录成功！", flush=True)
                            config_mgr.set("dashboard_url", current_url)
                            await self.context.storage_state(path=AUTH_STATE_FILE)
                            print("🔐 登录状态已安全保存，供下次后台巡逻使用。", flush=True)
                            self.page = p  # 切换主控页面到登录成功的那个标签页
                            if (
                                not is_background
                                and manual_login_page_seen
                                and not self.has_saved_wble_credentials
                            ):
                                await self.wait_for_password_save_confirmation(p)
                            elif (
                                not is_background
                                and manual_login_page_seen
                                and self.has_saved_wble_credentials
                            ):
                                print(
                                    "🔐 已检测到保存过的 WBLE 密码，"
                                    "跳过保存密码提醒。",
                                    flush=True
                                )
                            return True
                    except Exception:
                        pass
            await asyncio.sleep(1)

        if is_background:
            final_urls = ", ".join(p.url for p in self.context.pages)
            print(f"🔎 后台登录检测最终页面: {final_urls}", flush=True)
            print("⚠️ 幽灵模式检测到需要登录，终止后台扫描，请用户手动 Force Scan 授权。", flush=True)
        else:
            print("⚠️ 登录检测超时，请重试。", flush=True)
        return False

        
    async def run_scan_cycle(self):
        state_db = config_mgr.state
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🚀 开始执行深度巡逻...", flush=True)
        dashboard_url = config_mgr.get("dashboard_url", "https://wble.utar.edu.my/")
        await self.page.goto(dashboard_url)
        await self.page.wait_for_timeout(2000)
        
        courses = await extract_course_links(self.page)
        
        # update available courses in config to show in GUI
        config_mgr.set("available_courses", list(courses.keys()))
        
        updates_found = []
        for name, link in courses.items():
            print(f"➡️ 正在进入课程: {name[:40]}...", flush=True)
            try:
                safe_course_name = re.sub(r'[\\/*?:"<>|]', "_", name)
                
                base_dir = config_mgr.get("download_dir", os.path.join(os.getcwd(), "WBLE_Downloads"))
                course_dir = os.path.join(base_dir, safe_course_name)
                os.makedirs(course_dir, exist_ok=True)
                
                # 确保旧 state 也有新字段（兼容之前的用户数据）
                if name not in state_db:
                    state_db[name] = {}
                course_state = state_db[name]
                course_state.setdefault("hash", "")
                course_state.setdefault("downloaded_files", [])
                course_state.setdefault("md_generated", False)
                course_state.setdefault("ics_generated", False)
                course_state.setdefault("has_unclassified_files", False)
                if course_state.get("download_tracking_version") != 2:
                    if course_state["downloaded_files"]:
                        print(
                            "   🔄 下载状态规则升级：重新核验历史资源；"
                            "硬盘已有文件不会重复写入。",
                            flush=True,
                        )
                    course_state["downloaded_files"] = []
                    course_state["download_tracking_version"] = 2

                old_snapshot = course_state.get("content_snapshot")
                if not isinstance(old_snapshot, dict):
                    old_snapshot = None
                had_legacy_baseline = bool(course_state.get("hash"))
                is_first_scan = old_snapshot is None and not had_legacy_baseline

                new_files, text_content, new_snapshot, extraction_ok = (
                    await deep_scan_course(
                        self.page, link, course_dir, state_db, name
                    )
                )

                content_changed = False
                if extraction_ok:
                    current_hash = get_snapshot_hash(new_snapshot)
                    if old_snapshot is None:
                        course_state["content_snapshot"] = new_snapshot
                        course_state["snapshot_version"] = SNAPSHOT_VERSION
                        course_state["hash"] = current_hash
                        if is_first_scan:
                            print(
                                f"   [初次识别] 已建立结构化内容基线；"
                                f"本次深潜共挖出 {new_files} 份文件。",
                                flush=True
                            )
                        else:
                            # Existing hash-only users migrate silently. Comparing a
                            # raw-text hash with a structured hash would be a false alert.
                            print(
                                "   🔄 已从旧版纯哈希状态迁移到结构化快照，"
                                "本轮仅建立新基线，不发送更新通知。",
                                flush=True
                            )
                    elif (
                        old_snapshot.get("version") != SNAPSHOT_VERSION
                        or course_state.get("snapshot_version")
                        != SNAPSHOT_VERSION
                    ):
                        course_state["content_snapshot"] = new_snapshot
                        course_state["snapshot_version"] = SNAPSHOT_VERSION
                        course_state["hash"] = current_hash
                        print(
                            "   🔄 结构化快照规则已升级，本轮仅重建基线，"
                            "不发送更新通知。",
                            flush=True
                        )
                    elif course_state.get("hash") != current_hash:
                        diff_text = diff_course_snapshots(
                            old_snapshot, new_snapshot
                        )
                        print(
                            "   🚨 发现更新！结构化课程内容发生变化。",
                            flush=True
                        )
                        summary = await analyze_course_updates(
                            name, diff_text, text_content
                        )
                        updates_found.append({
                            "course": name,
                            "summary": summary,
                            "files_count": new_files
                        })
                        course_state["content_snapshot"] = new_snapshot
                        course_state["snapshot_version"] = SNAPSHOT_VERSION
                        course_state["hash"] = current_hash
                        course_state["md_generated"] = False
                        course_state["ics_generated"] = False
                        content_changed = True
                    else:
                        print("   [状态一致] 结构化课程内容暂无更新。", flush=True)
                else:
                    print(
                        "   🛡️ 已启用失败保护：旧快照、旧哈希和 MD "
                        "均保持不变。",
                        flush=True
                    )

                if (
                    new_files > 0
                    and not content_changed
                    and not is_first_scan
                ):
                    print(
                        f"   📦 课程文字未确认变化，但深潜抓到了 "
                        f"{new_files} 份新课件！",
                        flush=True
                    )
                    updates_found.append({
                        "course": name,
                        "summary": "课程文字未确认变化，但抓取到了新的课件文件。",
                        "files_count": new_files
                    })

                # ── 自愈机制 1：MD 归档（状态感知 + 文件系统双保险）────────
                md_path = os.path.join(course_dir, "课程重点归档.md")
                need_md = (
                    not course_state.get("md_generated", False)
                    or not os.path.exists(md_path)
                )
                if need_md and text_content:
                    print(f"   🔄 [自愈] MD 归档缺失或上次生成失败，正在重新生成...", flush=True)
                    md_ok = await generate_md_archive(name, text_content, course_dir)
                    course_state["md_generated"] = md_ok
                    if md_ok:
                        print(f"   ✅ [自愈] MD 归档补全成功！", flush=True)
                    else:
                        print(f"   ⚠️ [自愈] MD 生成仍然失败，请检查 API Key 配置，下次会继续重试。", flush=True)

                # ── 自愈机制 1.5：ICS 日历（仅当 MD 存在且 ICS 未成功时触发）────────
                need_ics = not state_db[name].get("ics_generated", False)
                if need_ics and os.path.exists(md_path):
                    print(f"   🔄 [自愈] ICS 日历未同步，正在自动对齐更新...", flush=True)
                    ics_ok = await generate_ics_calendar(name, course_dir)
                    course_state["ics_generated"] = ics_ok

                # ── 自愈机制 2：Others 重分类（状态感知 + 文件系统双保险）──
                others_dir = os.path.join(course_dir, "Files", "Others")
                has_others_flag = state_db[name].get("has_unclassified_files", False)
                others_files_exist = os.path.exists(others_dir) and bool([f for f in os.listdir(others_dir) if os.path.isfile(os.path.join(others_dir, f))])
                if (has_others_flag or others_files_exist) and os.path.exists(others_dir):
                    stuck_files = [f for f in os.listdir(others_dir) if os.path.isfile(os.path.join(others_dir, f))]
                    if stuck_files:
                        print(f"   🔄 [自愈] 发现 {len(stuck_files)} 个文件上次分类失败，重新召唤 AI 分类...", flush=True)
                        files_dir = os.path.join(course_dir, "Files")
                        all_reclassified = True
                        for fname in stuck_files:
                            old_path = os.path.join(others_dir, fname)
                            category, ai_ok = await categorize_file_with_ai(fname, fname)
                            if not ai_ok:
                                all_reclassified = False  # AI 仍然失败，下次继续重试
                                print(f"      ⚠️ {fname} 重分类失败（API 仍不可用），保留在 Others。", flush=True)
                                continue
                            new_dir = os.path.join(files_dir, category)
                            os.makedirs(new_dir, exist_ok=True)
                            new_path = os.path.join(new_dir, fname)
                            if os.path.exists(new_path):
                                # 目标路径已有同名文件（正确位置已存在），直接删掉 Others 里的重复副本
                                os.remove(old_path)
                                print(f"      ✅ 目标已存在，清除 Others 重复副本: {fname}", flush=True)
                            else:
                                os.rename(old_path, new_path)
                                print(f"      ✅ 重新分类: {fname} -> {category}", flush=True)
                        state_db[name]["has_unclassified_files"] = not all_reclassified
                        # 如果 Others 文件夹已空，删掉保持整洁
                        if os.path.exists(others_dir) and not os.listdir(others_dir):
                            os.rmdir(others_dir)
                    else:
                        state_db[name]["has_unclassified_files"] = False

            except Exception as e:
                print(f"   ❌ 访问或处理课程失败: {e}", flush=True)
            finally:
                # Persist after each course so a later crash cannot discard the
                # completed downloads and snapshots from earlier courses.
                config_mgr.state = state_db
                config_mgr.save_state()
        
        config_mgr.state = state_db
        config_mgr.save_state()
        
        if updates_found:
            title = f"🚨 WBLE发现 {len(updates_found)} 门课有更新！"
            desp = "以下是详细的更新汇总：\n\n"
            for item in updates_found:
                desp += f"### 📘 {item['course']}\n"
                desp += f"> {item['summary']}\n"
                if item['files_count'] > 0:
                    desp += f"> 📦 **自动深潜为您抓取了 {item['files_count']} 份新文件，已存入电脑！**\n"
                desp += "\n---\n"
            await send_wechat_notification(title, desp)
        
        print(f"✅ 本轮深潜任务完成。", flush=True)
        return updates_found

    async def cleanup(self):
        context = self.context
        playwright = self.playwright
        self.context = None
        self.playwright = None
        self.page = None

        try:
            if context:
                await context.close()
        finally:
            if playwright:
                await playwright.stop()
