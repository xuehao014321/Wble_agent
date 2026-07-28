import asyncio
import os
import sys
import io
import time
import json
import hashlib
import re
import urllib.request
import urllib.parse
from datetime import datetime
from playwright.async_api import async_playwright
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from core.config import config_mgr

# 强制输出为 utf-8，解决 Windows 终端 GBK 报错 (仅在有控制台时)
if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TARGET_URL = "https://wble.utar.edu.my/"

# ================= 工具函数 =================

def send_wechat_notification(title, desp):
    serverchan_key = config_mgr.get("serverchan_key", "")
    if not serverchan_key:
        return
    print(f"📲 准备发送微信推送: {title}", flush=True)
    url = f"https://sctapi.ftqq.com/{serverchan_key}.send"
    data = urllib.parse.urlencode({'title': title, 'desp': desp}).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    try:
        urllib.request.urlopen(req)
        print("✅ 微信推送成功！", flush=True)
    except Exception as e:
        print(f"❌ 微信推送失败: {e}", flush=True)

def get_text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def is_valid_course(title: str) -> bool:
    title = title.strip()
    if not re.match(r'^[A-Z]{4}\d{4}', title):
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
    优先级: 1. Azure GPT-4o (每日免费50次) -> 2. 用户自备 Kimi -> 3. 用户自备 Gemini
    """
    # 1. Azure OpenAI (GitHub Models 白嫖)
    try:
        github_key = config_mgr.get("api_keys", {}).get("openai", "")
        if github_key:
            print("   🤖 [AI 引擎] 尝试主引擎: GitHub Models GPT-4o (User Token)...", flush=True)
            llm = ChatOpenAI(model="gpt-4o", api_key=github_key, base_url="https://models.inference.ai.azure.com", temperature=0.1)
        else:
            raise ValueError("未配置 GitHub Token，自动回退到下一个可用引擎")
        chain = prompt_template | llm
        res = await chain.ainvoke(kwargs_dict)
        return res.content
    except Exception as e:
        print(f"      ⚠️ Azure 主引擎已枯竭 (触发限制): {type(e).__name__}", flush=True)

    # 2. Groq (免费高速引擎)
    try:
        groq_key = config_mgr.get("api_keys", {}).get("groq", "")
        if groq_key:
            print("   🤖 [AI 引擎] 启动备用引擎: Groq (Llama 3)...", flush=True)
            llm = ChatOpenAI(model="llama-3.3-70b-versatile", api_key=groq_key, base_url="https://api.groq.com/openai/v1", temperature=0.1)
            chain = prompt_template | llm
            res = await chain.ainvoke(kwargs_dict)
            return res.content
    except Exception as e:
        print(f"      ⚠️ Groq 引擎调用失败: {type(e).__name__}", flush=True)

    # 3. Gemini (备用引擎，不花钱)
    try:
        print("   🤖 [AI 引擎] 启动备用引擎: Gemini 3.5 Flash...", flush=True)
        from google import genai
        gemini_key = config_mgr.get("api_keys", {}).get("gemini", "")
        client = genai.Client(api_key=gemini_key)
        
        prompt_text = prompt_template.format(**kwargs_dict)
        interaction = client.interactions.create(
            model="gemini-3.5-flash",
            input=prompt_text
        )
        return interaction.output_text
    except ImportError:
        print("      ⚠️ 未安装最新版 google-genai，无法调用 Gemini。", flush=True)
    except Exception as e:
        print(f"      ⚠️ Gemini 备用引擎调用失败: {type(e).__name__} - {e}", flush=True)

    # 3. Kimi (最终兜底防线，需扣费)
    try:
        print("   🤖 [AI 引擎] 启动最终兜底防线: Kimi (Moonshot)...", flush=True)
        kimi_key = config_mgr.get("api_keys", {}).get("kimi", "")
        llm = ChatOpenAI(
            model="kimi-k2.6", 
            base_url="https://api.moonshot.cn/v1", 
            api_key=kimi_key,
            temperature=1.0
        )
        chain = prompt_template | llm
        res = await chain.ainvoke(kwargs_dict)
        return res.content
    except Exception as e:
        print(f"      ⚠️ Kimi 调用失败: {type(e).__name__}", flush=True)
        
    raise Exception("所有大模型引擎均已阵亡，请补充额度。")


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


async def generate_initial_archive(course_name, text_content, course_dir):
    print(f"   📝 正在让大模型提炼【{course_name}】的极致精简笔记...", flush=True)
    try:
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
            "【原始内容】:\n{web_content}"
        )
        result_content = await invoke_llm_with_fallback(prompt, {"web_content": text_content[:6000]})
        
        md_file_path = os.path.join(course_dir, "课程重点归档.md")
        with open(md_file_path, "w", encoding="utf-8") as f:
            f.write(f"# {course_name}\n")
            f.write(f"*归档时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
            f.write(result_content)
            
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
            "BEGIN:VEVENT\nSUMMARY:Assignment 1\nDTSTART:20260810T150000Z\nDTEND:20260810T160000Z\nEND:VEVENT\n"
            "END:VCALENDAR\n\n"
            "注意：如果没有具体结束时间，可以默认结束时间比开始时间晚一小时。如果只有日期没有具体时间，可以默认为当地时间中午12点。必须输出标准的 ics 文本格式。\n\n"
            "【课程重点笔记】:\n{web_content}"
        )
        try:
            ics_content = await invoke_llm_with_fallback(ics_prompt, {"web_content": result_content})
            ics_content = ics_content.strip()
            
            # 使用正则严格提取日历文本块，无视大模型生成的啰嗦废话
            match = re.search(r'(BEGIN:VCALENDAR.*?END:VCALENDAR)', ics_content, re.DOTALL | re.IGNORECASE)
            
            if match:
                ics_path = os.path.join(course_dir, "Reminder.ics")
                with open(ics_path, "w", encoding="utf-8") as f:
                    f.write(match.group(1).strip())
                print("   ✅ 日历文件 Reminder.ics 生成成功！", flush=True)
            else:
                print("   ℹ️ 未在课件主页发现具体的截止日期，跳过日历生成。", flush=True)
        except Exception as e:
            print(f"   ⚠️ 日历生成失败: {e}", flush=True)
            
        return True  # 成功
    except Exception:
        print(f"   ⚠️ 笔记生成跳过 (所有大模型引擎均失败或无额度)。", flush=True)
        return False  # 失败，需要下次重试


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


async def download_from_current_page(page, course_dir, state_db, course_name):
    files_dir = os.path.join(course_dir, "Files")
    os.makedirs(files_dir, exist_ok=True)
    
    links_count = await page.locator("a[href*='mod/resource/view.php']").count()
    new_files_count = 0
    
    for i in range(links_count):
        link_el = page.locator("a[href*='mod/resource/view.php']").nth(i)
        href = await link_el.evaluate("node => node.href")
        
        # 提取链接的可见文字，提供给 AI 做上下文分析
        link_text = (await link_el.inner_text()).strip().replace("\n", " ")
        
        if href and href not in state_db[course_name]["downloaded_files"]:
            print(f"      ⬇️ [发现目标] 启动底层网络引擎拦截... ({link_text[:30]})", flush=True)
            
            try:
                response = await page.context.request.get(href)
                content_type = response.headers.get("content-type", "")
                final_url = response.url
                
                if "text/html" in content_type:
                    html_body = await response.text()
                    file_urls = re.findall(r'https?://[^"\'<>\s]+file\.php/[^"\'<>\s]+', html_body)
                    file_urls = list(set(file_urls))
                    
                    if not file_urls:
                        print(f"        ⚠️ 预览页里没找到 file.php 链接，跳过。", flush=True)
                        state_db[course_name]["downloaded_files"].append(href)
                        continue
                    
                    for file_url in file_urls:
                        fname = urllib.parse.unquote(file_url.split('/')[-1].split('?')[0])
                        safe_fn = re.sub(r'[\\/*?:"<>|]', "_", fname)
                        
                        ext = safe_fn.split('.')[-1].lower()
                        if ext in ['mp4', 'mkv', 'avi', 'mov']:
                            print(f"        ⚠️ 跳过视频: {safe_fn}", flush=True)
                            continue
                            
                        category = smart_categorize_local(safe_fn, link_text)
                        if not category:
                            print(f"        🧠 正则匹配失败，召唤 AI 兜底推理 [{safe_fn}]...", flush=True)
                            category, ai_ok = await categorize_file_with_ai(safe_fn, link_text)
                            if not ai_ok:
                                state_db[course_name]["has_unclassified_files"] = True
                        else:
                            print(f"        ⚡ 本地急速分类 [{safe_fn}] -> {category}", flush=True)
                            
                        section_dir = os.path.join(files_dir, category)
                        os.makedirs(section_dir, exist_ok=True)
                        save_path = os.path.join(section_dir, safe_fn)
                        
                        if os.path.exists(save_path):
                            print(f"        ⏭️ 硬盘上已存在该文件，跳过下载: {category}/{safe_fn}", flush=True)
                            continue
                        
                        try:
                            file_resp = await page.context.request.get(file_url)
                            body_bytes = await file_resp.body()
                            max_size_bytes = config_mgr.get("max_file_size_mb", 50) * 1024 * 1024
                            
                            if len(body_bytes) > max_size_bytes:
                                print(f"        ⚠️ 文件过大 ({len(body_bytes)/1024/1024:.1f}MB)，超过设定上限，已跳过下载: {safe_fn}", flush=True)
                                continue
                                
                            with open(save_path, "wb") as f:
                                f.write(body_bytes)
                            print(f"        ✅ 下载完成: {category}/{safe_fn}", flush=True)
                            new_files_count += 1
                        except Exception as inner_e:
                            print(f"        ⚠️ 下载失败: {inner_e}", flush=True)
                    
                    state_db[course_name]["downloaded_files"].append(href)
                    continue
                
                # 直接返回文件的情况
                filename = final_url.split('/')[-1].split('?')[0]
                if not filename or filename == "view.php":
                    disp = response.headers.get("content-disposition", "")
                    if "filename=" in disp:
                        filename = disp.split("filename=")[-1].strip('"')
                    else:
                        filename = f"downloaded_{int(time.time())}.pdf"
                filename = urllib.parse.unquote(filename)
                safe_filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
                
                ext = safe_filename.split('.')[-1].lower()
                if ext in ['mp4', 'mkv', 'avi', 'mov']:
                    print(f"      ⚠️ 跳过视频: {safe_filename}", flush=True)
                else:
                    category = smart_categorize_local(safe_filename, link_text)
                    if not category:
                        print(f"      🧠 正则匹配失败，召唤 AI 兜底推理 [{safe_filename}]...", flush=True)
                        category, ai_ok = await categorize_file_with_ai(safe_filename, link_text)
                        if not ai_ok:
                            state_db[course_name]["has_unclassified_files"] = True
                    else:
                        print(f"      ⚡ 本地急速分类 [{safe_filename}] -> {category}", flush=True)
                        
                    section_dir = os.path.join(files_dir, category)
                    os.makedirs(section_dir, exist_ok=True)
                    
                    save_path = os.path.join(section_dir, safe_filename)
                    if os.path.exists(save_path):
                        print(f"      ⏭️ 硬盘上已存在该文件，跳过下载: {category}/{safe_filename}", flush=True)
                    else:
                        body_bytes = await response.body()
                        max_size_bytes = config_mgr.get("max_file_size_mb", 50) * 1024 * 1024
                        if len(body_bytes) > max_size_bytes:
                            print(f"      ⚠️ 文件过大 ({len(body_bytes)/1024/1024:.1f}MB)，超过设定上限，已跳过下载: {safe_filename}", flush=True)
                        else:
                            with open(save_path, "wb") as f:
                                f.write(body_bytes)
                            print(f"      ✅ 下载完成: {category}/{safe_filename}", flush=True)
                            new_files_count += 1
                
                state_db[course_name]["downloaded_files"].append(href)
            except Exception as e:
                print(f"      ⚠️ 下载跳过 (网络错误: {e})", flush=True)
                state_db[course_name]["downloaded_files"].append(href)
        elif href:
            # 已经在 json 里记录过了，直接跳过并打印
            print(f"      ⏭️  数据库中已有记录，跳过无需下载: {link_text[:40]}", flush=True)
            
    return new_files_count

async def deep_scan_course(page, course_link, course_dir, state_db, course_name):
    """深潜抓取机制：扫描主页以及所有文件夹子页面"""
    # 1. 抓取课程主页直连的文件
    await page.goto(course_link, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    
    # 尝试仅抓取主要课程区域，避免侧边栏（最新活动、在线用户、时间戳）导致频繁的 Hash 误报
    text_content = ""
    # 优先匹配 middle-column (WBLE经典主题结构)，再匹配 region-main (Moodle新版Boost主题)
    for selector in ["#middle-column", ".middle-column", "#region-main", ".course-content", "[role='main']", "body"]:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible():
                text_content = await locator.inner_text()
                break
        except Exception:
            continue
            
    if not text_content:
        text_content = await page.inner_text("body")
    
    
    # 抓取页面中的外部会议/文档链接，注入到纯文本供大模型分析
    external_links = await page.evaluate('''() => {
        const keywords = ['teams.microsoft', 'docs.google', 'drive.google', 'zoom.us', 'webex', 'meet.google', 'chat.whatsapp'];
        const links = Array.from(document.querySelectorAll('a'));
        return links
            .filter(a => keywords.some(k => a.href.includes(k)))
            .map(a => `- [${a.innerText.trim() || 'Link'}](${a.href})`);
    }''')
    if external_links:
        unique_links = list(set(external_links))
        text_content += "\n\n【页面中提取到的重要外部链接】:\n" + "\n".join(unique_links)
        
    total_new_files = 0
    total_new_files += await download_from_current_page(page, course_dir, state_db, course_name)
    
    # 2. 寻找潜藏的子页面 (比如 Moodle 的 Folder 插件)
    sub_links_locators = await page.locator("a[href*='mod/folder/view.php'], a[href*='mod/page/view.php']").all()
    sub_hrefs = []
    for el in sub_links_locators:
        href = await el.get_attribute("href")
        if href and href not in sub_hrefs:
            sub_hrefs.append(href)
            
    # 3. 执行深潜
    for sub_href in sub_hrefs:
        print(f"      🤿 正在深潜进入子网页: {sub_href.split('id=')[-1]}", flush=True)
        try:
            await page.goto(sub_href, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            total_new_files += await download_from_current_page(page, course_dir, state_db, course_name)
        except Exception:
            print("      ⚠️ 深潜失败，跳过该子页面", flush=True)
            
    # 最后一定要回到主页以便抓取文本作后续 Diff (或者直接用第一次抓取的 text_content)
    return total_new_files, text_content

async def analyze_course_updates(course_name, new_text):
    try:
        prompt = PromptTemplate.from_template(
            "我抓取了同一门课【{course_name}】的最新网页内容。\n"
            "请对比并总结出：老师【刚刚】发布了什么新内容？（可能是新公告，或是新布置的作业）。\n"
            "请用极其精简的一句话或列表总结。\n\n【网页内容】:\n{new_text}"
        )
        result_content = await invoke_llm_with_fallback(prompt, {"course_name": course_name, "new_text": new_text[:6000]})
        return result_content
    except Exception as e:
        print(f"   ⚠️ 大模型分析更新失败 (所有引擎均失败或无额度): {e}", flush=True)
        return "网页有更新，但所有 AI 引擎额度耗尽，未能自动总结。"

class WBLEScanner:
    def __init__(self):
        self.playwright = None
        self.context = None
        self.page = None
        
    async def init_browser(self):
        print("🤖 初始化浏览器引擎...", flush=True)
        self.playwright = await async_playwright().start()
        user_data_dir = os.path.join(os.getcwd(), "chrome_data")
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                channel="chrome",
                ignore_https_errors=True
            )
        except Exception as e:
            print(f"⚠️ 未找到系统 Chrome 浏览器，正在自动为你下载便携版引擎... (请耐心等待)", flush=True)
            import subprocess
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                ignore_https_errors=True
            )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        
    async def wait_for_login(self):
        await self.page.goto(TARGET_URL)
        print("\n" + "❗"*25, flush=True)
        print("🛑 登录状态检查：", flush=True)
        print("👉 请在浏览器里选择校区并完成登录，登录成功后脚本会自动开始工作！", flush=True)
        print("👉 ⚠️ 温馨提示：选择校区后会弹出新标签页，属于正常现象，请在新标签页完成登录！", flush=True)
        print("❗"*25 + "\n", flush=True)
        
        for _ in range(600):
            # 关键修复：监控 context 里所有标签页，而不只是初始页
            all_pages = self.context.pages
            for p in all_pages:
                current_url = p.url
                if ("wble" in current_url and ".utar.edu.my" in current_url 
                        and "login" not in current_url 
                        and current_url.rstrip("/") != "https://wble.utar.edu.my"):
                    try:
                        logout_count = await p.locator("a[href*='logout.php']").count()
                        course_count = await p.locator("a[href*='course/view.php']").count()
                        if logout_count > 0 or course_count > 0:
                            print("\n✅ 已自动检测到登录成功！", flush=True)
                            config_mgr.set("dashboard_url", current_url)
                            self.page = p  # 切换主控页面到登录成功的那个标签页
                            return True
                    except Exception:
                        pass
            await asyncio.sleep(1)
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
                    state_db[name] = {"hash": "", "downloaded_files": [], "md_generated": False, "has_unclassified_files": False}
                else:
                    state_db[name].setdefault("md_generated", False)
                    state_db[name].setdefault("has_unclassified_files", False)

                if state_db[name]["hash"] == "":  # 真正初次扫描
                    print(f"   [初次识别] 激活大模型归档与深潜下载模式！", flush=True)
                    new_files, text_content = await deep_scan_course(self.page, link, course_dir, state_db, name)
                    md_ok = await generate_initial_archive(name, text_content, course_dir)
                    state_db[name]["md_generated"] = md_ok
                    current_hash = get_text_hash(text_content)
                    state_db[name]["hash"] = current_hash
                    print(f"   🎉 归档完毕！本次深潜共挖出 {new_files} 份文件。", flush=True)
                else:
                    new_files, text_content = await deep_scan_course(self.page, link, course_dir, state_db, name)
                    current_hash = get_text_hash(text_content)
                    if state_db[name]["hash"] != current_hash:
                        print(f"   🚨 发现更新！网页文字发生变化。", flush=True)
                        summary = await analyze_course_updates(name, text_content)
                        updates_found.append({
                            "course": name,
                            "summary": summary,
                            "files_count": new_files
                        })
                        state_db[name]["hash"] = current_hash
                    elif new_files > 0:
                        print(f"   📦 网页文字虽未变，但在子文件夹深潜抓到了 {new_files} 份新课件！", flush=True)
                        updates_found.append({
                            "course": name,
                            "summary": "文字无大更新，但抓取到了隐藏的新课件文件。",
                            "files_count": new_files
                        })
                    else:
                        print(f"   [状态一致] 暂无更新。", flush=True)

                # ── 自愈机制 1：MD 归档（状态感知 + 文件系统双保险）────────
                md_path = os.path.join(course_dir, "课程重点归档.md")
                need_md = not state_db[name].get("md_generated", False) or not os.path.exists(md_path)
                if need_md and text_content:
                    print(f"   🔄 [自愈] MD 归档缺失或上次生成失败，正在重新生成...", flush=True)
                    md_ok = await generate_initial_archive(name, text_content, course_dir)
                    state_db[name]["md_generated"] = md_ok
                    if md_ok:
                        print(f"   ✅ [自愈] MD 归档补全成功！", flush=True)
                    else:
                        print(f"   ⚠️ [自愈] MD 生成仍然失败，请检查 API Key 配置，下次会继续重试。", flush=True)

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
            send_wechat_notification(title, desp)
        
        print(f"✅ 本轮深潜任务完成。", flush=True)
        return updates_found

    async def cleanup(self):
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
