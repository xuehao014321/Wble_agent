<div align="center">
  <img src="utar_logo.png" width="120" height="120" alt="UTAR WBLE Agent Logo">
  <h1>UTAR WBLE Agent</h1>
  <p><strong>A silent WBLE course monitor, downloader, and AI update assistant for UTAR students.</strong></p>
</div>

---

## Overview

UTAR WBLE Agent monitors the Kampar and Sungai Long WBLE portals for course
updates. It can download newly published files, detect meaningful changes in
the course content column, generate Markdown notes and calendar events, and
optionally send update notifications through ServerChan.

The application is designed for Windows 10/11 and runs from the system tray.

## Main Features

- **Two scan modes**
  - **Force Scan:** opens a visible browser for first-time login, expired
    sessions, or manual verification.
  - **Background Scan:** launches a headless browser and scans silently without
    stealing keyboard focus.
- **Scheduled patrols:** choose 30 minutes, 1 hour, 4 hours, or 12 hours.
- **Startup scan:** when Silent Startup is enabled, Windows starts the agent in
  the tray and immediately performs one background scan.
- **Login-session reuse:** WBLE cookies and the dedicated Chrome profile are
  retained between runs. A background scan stops quickly and asks for a Force
  Scan if login has expired.
- **Stable course change detection:** reads the structured
  `#middle-column` course area and ignores dynamic sidebar content such as
  recent activity, timestamps, and online users.
- **Reliable downloads:** authenticated streaming downloads, configurable size
  limits, partial-file cleanup, filename sanitization, and automatic retry
  after network failures.
- **AI fallback chain:** GitHub Models, Groq, Google Gemini, and Kimi can
  summarize confirmed course changes and organize downloaded material.
- **Markdown and calendar output:** long course content is processed in chunks;
  generated ICS events use the `Asia/Kuala_Lumpur` timezone.
- **Optional WeChat notifications:** ServerChan requests run asynchronously so
  they do not freeze the interface.
- **Safe local data handling:** API keys are protected with Windows DPAPI,
  configuration writes are atomic, and deleted course folders are moved to the
  Recycle Bin.
- **Single-instance protection:** prevents duplicate tray agents from running
  at the same time.

## Install a Release

1. Download `UTAR_WBLE_Agent.exe` from the repository's
   [Releases](https://github.com/xuehao014321/Wble_agent/releases) page.
2. Place the EXE in a permanent folder.
3. Install Google Chrome if it is not already available.
4. Run the EXE.
5. Enter at least one supported AI key and click **Save Preferences**.
6. Click **Force Scan Now** and complete the WBLE login in the visible browser.
7. After the first successful scan, the scheduled background patrol can reuse
   that login session.

If a notification says the background browser requires login, open the main
window and perform one Force Scan. The application does not need to click the
page every 30 seconds; it keeps the browser profile and starts a fresh,
authenticated browser for each scheduled scan.

## Configuration

The following AI providers are supported:

- **GitHub Models:** a GitHub PAT with model-read permission.
- **Groq:** an API key beginning with `gsk_`.
- **Google Gemini:** an AI Studio API key.
- **Kimi / Moonshot:** an API key beginning with `sk-`.

Only one AI provider is required. ServerChan is optional.

Application settings, encrypted API keys, WBLE state, Chrome profile, and
rotating logs are stored in:

```text
%LOCALAPPDATA%\UTAR_WBLE_Agent
```

Downloaded course files default to:

```text
%USERPROFILE%\Downloads\WBLE_Downloads
```

Do not copy the AppData profile to another Windows account: DPAPI-protected
secrets can only be decrypted by the Windows user who saved them.

- 🏫 **Dynamic Campus Support:** Universally works across `wble-kpr` (Kampar) and `wble-sl` (Sungai Long) domains. Automatically saves your specific dashboard context upon successful login.
- 🧠 **AI-Powered Summarization:** Integrates seamlessly with GPT-4o (via GitHub Free Token), **Groq (Llama 3)**, Google Gemini, and Kimi (Moonshot) to automatically read and summarize dense course announcements into bite-sized actionable notes.
- 📅 **Actionable Calendar Export (`Reminder.ics`):** Automatically extracts critical deadlines, exam schedules, and replacement/additional classes, outputting a standard `.ics` file. Perfect for syncing directly with Windows Calendar, Outlook, or Apple Calendar.
- 🎯 **Pixel-Perfect Updates Detection:** Exclusively monitors the core `#middle-column` course content area, effectively ignoring false positives triggered by "Recent Activity", timestamps, and "Online Users" in the dynamic Moodle sidebar.
- 📲 **WeChat Push Notifications:** Hooks into Server酱 API to send real-time push notifications of course updates straight to your phone.
- 📦 **Automated File Downloads:** Deep-scans hidden sub-folders (Moodle Folders) and downloads all newly uploaded PDFs/PPTs straight to your PC. Limits large file downloads to save space and bandwidth.
- 📂 **Quick Folder Portal:** Simply **double-click** any monitored course name on the left sidebar to instantly pop open its download folder in Windows Explorer.
- 💻 **Modern Minimalist UI:** Built with PyQt6 featuring an Apple-inspired glassmorphism aesthetic. Contains easy "idiot-proof" (保姆级) pop-up tutorials for setting up all required API keys.
## Run from Source

```powershell
git clone https://github.com/xuehao014321/Wble_agent.git
cd Wble_agent

1. Download the latest `UTAR_WBLE_Agent_Release.zip` from the Releases section.
2. Unzip the folder to your preferred location.
3. Double click on **`UTAR_WBLE_Agent.exe`** to launch the application. *(No Python installation or command line required!)*
4. Fill in the **Required API Keys**:
    - **GitHub PAT:** Used to access GPT-4o entirely for free.
    - **Groq API Key:** Access lightning-fast Llama 3 models entirely for free.
    - **Kimi / Gemini:** Used as alternative fast-inference AI engines.
    - *(Note: The application has strict validation logic to ensure formats match `ghp_`, `github_pat_`, `gsk_`, `sk-`, or `AIza`.)*
5. Click **"Save Preferences"** and then **"Force Scan Now"**. The app will bring up an automated browser to let you choose your campus and log in. Afterwards, it runs completely on autopilot!
python -m pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

The source build prefers installed Google Chrome and uses Playwright Chromium
as a fallback.

## Run Tests

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py core gui tests
```

The regression tests cover DPAPI protection, atomic configuration recovery,
course recognition, structured content diffs, calendar validation, and
streaming download limits.

## Build the Windows EXE

Use the checked-in PyInstaller spec so the PNG interface logo, multi-resolution
EXE icon, and scan-success animation are all included:

```powershell
pyinstaller --clean -y UTAR_WBLE_Agent.spec
```

Output:

```text
dist\UTAR_WBLE_Agent.exe
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
Before publishing, fully exit any old WBLE Agent instance from the system tray
and run the tests again. Never store GitHub tokens or other credentials in
source files or release scripts.

## Troubleshooting

### Background scan says login is required

Open the main interface and click **Force Scan Now**. Log in once in the visible
browser, allow the scan to complete, and future scheduled scans will reuse the
saved profile.

### Chrome profile is locked

Exit all WBLE Agent instances from the tray and retry. The current release
prevents new duplicate instances, but an older build may still be running.

### The application appears to close

Closing the main window hides it in the system tray. Use the tray menu's
**Completely Exit** action to stop scheduled scans.

### A large file was skipped

Increase **Max File Limit (MB)** in Settings if the file is trusted and enough
disk space is available. Oversized or interrupted downloads remain retryable.

## License

This project is intended for educational and personal productivity use by UTAR
students.
