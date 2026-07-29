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

## Run from Source

```powershell
git clone https://github.com/xuehao014321/Wble_agent.git
cd Wble_agent

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
