<div align="center">
  <img src="utar_logo.png" width="120" height="120" alt="UTAR WBLE Agent Logo">
  <h1>UTAR WBLE Auto-Agent 🤖🎓</h1>
  <p><strong>A fully automated, AI-powered assistant for UTAR students to scrape, summarize, and monitor WBLE course materials.</strong></p>
</div>

---

## 🌟 Introduction

The **UTAR WBLE Agent** is a desktop application designed to rescue UTAR students from the tedious task of constantly checking the WBLE portal for new lecture notes, assignments, or announcements. 

It runs silently in the background, acts as an automated browser, logs in on your behalf (supports both Kampar and Sungai Long campuses), and intelligently monitors the core course content areas. If a lecturer uploads a new file or posts an update, it instantly downloads the file and uses Advanced Large Language Models (LLMs) to summarize the update, pushing notifications directly to your WeChat via Server酱.

## 🚀 Features

- 🏫 **Dynamic Campus Support:** Universally works across `wble-kpr` (Kampar) and `wble-sl` (Sungai Long) domains. Automatically saves your specific dashboard context upon successful login.
- 🧠 **AI-Powered Summarization:** Integrates seamlessly with GPT-4o (via GitHub Free Token), **Groq (Llama 3)**, Google Gemini, and Kimi (Moonshot) to automatically read and summarize dense course announcements into bite-sized actionable notes.
- 📅 **Actionable Calendar Export (`Reminder.ics`):** Automatically extracts critical deadlines, exam schedules, and replacement/additional classes, outputting a standard `.ics` file. Perfect for syncing directly with Windows Calendar, Outlook, or Apple Calendar.
- 🎯 **Pixel-Perfect Updates Detection:** Exclusively monitors the core `#middle-column` course content area, effectively ignoring false positives triggered by "Recent Activity", timestamps, and "Online Users" in the dynamic Moodle sidebar.
- 📲 **WeChat Push Notifications:** Hooks into Server酱 API to send real-time push notifications of course updates straight to your phone.
- 📦 **Automated File Downloads:** Deep-scans hidden sub-folders (Moodle Folders) and downloads all newly uploaded PDFs/PPTs straight to your PC. Limits large file downloads to save space and bandwidth.
- 📂 **Quick Folder Portal:** Simply **double-click** any monitored course name on the left sidebar to instantly pop open its download folder in Windows Explorer.
- 💻 **Modern Minimalist UI:** Built with PyQt6 featuring an Apple-inspired glassmorphism aesthetic. Contains easy "idiot-proof" (保姆级) pop-up tutorials for setting up all required API keys.

## 🛠️ Installation & Usage

1. Download the latest `UTAR_WBLE_Agent_Release.zip` from the Releases section.
2. Unzip the folder to your preferred location.
3. Double click on **`UTAR_WBLE_Agent.exe`** to launch the application. *(No Python installation or command line required!)*
4. Fill in the **Required API Keys**:
    - **GitHub PAT:** Used to access GPT-4o entirely for free.
    - **Groq API Key:** Access lightning-fast Llama 3 models entirely for free.
    - **Kimi / Gemini:** Used as alternative fast-inference AI engines.
    - *(Note: The application has strict validation logic to ensure formats match `ghp_`, `github_pat_`, `gsk_`, `sk-`, or `AIza`.)*
5. Click **"Save Preferences"** and then **"Force Scan Now"**. The app will bring up an automated browser to let you choose your campus and log in. Afterwards, it runs completely on autopilot!

## 🔧 Developer Setup (For Source Code)

If you'd like to tinker with the code yourself:

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/utar-wble-agent.git
cd utar-wble-agent

# 2. Install dependencies
pip install -r requirements.txt

# 3. Ensure Playwright browser binaries are installed
playwright install chromium

# 4. Run the agent
python main.py
```

## 📦 Building from Source (PyInstaller)

To compile the project into a standalone executable (`.exe`) optimized for extremely fast startup and low file size, run:

```powershell
pyinstaller --noconsole --name UTAR_WBLE_Agent --icon utar_logo.png --add-data "utar_logo.png;." --exclude-module PyQt5 --exclude-module torch --exclude-module scipy --exclude-module pandas --exclude-module pyarrow --exclude-module onnxruntime --exclude-module matplotlib --exclude-module botocore -y main.py
```

## 🎁 Recent Upgrades & PR Patches

This section documents the latest updates, bug fixes, and feature additions included in this pull request for easier review:

### 🌟 New Features
1. **Groq (Llama 3) Integration**: 
   - Added **Groq API Key** configuration to the right-hand preference panel.
   - Automatically falls back to Groq (`llama-3.3-70b-versatile`) as a primary free high-speed backup engine right after the GitHub GPT-4o engine.
2. **Actionable Agenda Generator (`Reminder.ics`)**: 
   - Distills raw updates into a standardized `.ics` calendar format to keep track of deadlines, test/quiz times, and replacement or additional classes.
   - Built-in strict exclusion rules to ignore pure cancellation notices (unless accompanied by rescheduled timings), avoiding calendar clutter.
3. **Double-Click Course Shortcut**:
   - Double-clicking any course list item in the left-hand panel now instantly opens its corresponding folder path in Windows Explorer.

### 🐛 Critical Bug Fixes & Refactoring
1. **Gemini SDK Crash**: Fixed hallucinated API syntax (`client.interactions.create` and model `gemini-3.5-flash`) by porting it to the standard and robust `langchain-google-genai` integration with `gemini-2.0-flash`.
2. **Kimi Model Config**: Corrected the Kimi engine model name from the non-existent `kimi-k2.6` to the standard Moonshot model `moonshot-v1-8k`.
3. **Configuration Key Inconsistency**: Resolved mismatched setting namespaces between `azure_github` (config.py) and `openai` (main_window.py).
4. **Dependency Patch**: Added the missing PyQt-asyncio bridge library `qasync` to `requirements.txt`.

## 📝 License

This project is open-source and intended purely for educational and personal productivity purposes for UTAR students. 
