import sys
import os
import re
import asyncio
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QLabel, QLineEdit, QTextEdit, QFileDialog, QCheckBox, 
    QSystemTrayIcon, QMenu, QListWidget, QListWidgetItem, QSlider, QComboBox,
    QMessageBox, QSplitter, QGraphicsOpacityEffect, QSizePolicy, QToolButton, QAbstractItemView
)
from PyQt6.QtGui import QIcon, QDesktopServices, QAction, QColor, QPalette, QPainter, QPainterPath
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QUrl, QPropertyAnimation, QEasingCurve, QTimer, QSize, QPoint

from core.config import config_mgr
from core.engine import WBLEScanner

class LogStream(QObject):
    textWritten = pyqtSignal(str)

    def write(self, text):
        self.textWritten.emit(str(text))
        
    def flush(self):
        pass

class ElidedLabel(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        metrics = self.fontMetrics()
        elided = metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

class ToastNotification(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.SubWindow | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        
        self.label = QLabel()
        self.label.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 绿色勾选图标
        icon_label = QLabel("✅")
        icon_label.setStyleSheet("font-size: 16px;")
        
        layout.addWidget(icon_label)
        layout.addWidget(self.label)
        
        self.opacity_effect = QGraphicsOpacityEffect()
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)
        
        self.animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.animation.setDuration(300)
        
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_toast)
        
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 10, 10)
        painter.fillPath(path, QColor(40, 40, 40, 230))
        
    def show_toast(self, message, duration=3000):
        self.label.setText(message)
        self.adjustSize()
        if self.parent():
            parent_rect = self.parent().rect()
            # 居中显示在底部上方 50px
            self.move(parent_rect.center().x() - self.width() // 2, parent_rect.bottom() - 80)
        
        self.show()
        self.animation.setDirection(QPropertyAnimation.Direction.Forward)
        self.animation.start()
        self.timer.start(duration)
        
    def hide_toast(self):
        self.animation.setDirection(QPropertyAnimation.Direction.Backward)
        self.animation.start()
        self.animation.finished.connect(self._on_hide_finished)
        
    def _on_hide_finished(self):
        self.animation.finished.disconnect(self._on_hide_finished)
        self.hide()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UTAR WBLE Agent - macOS Edition")
        self.resize(1000, 650)
        self.setWindowIcon(QIcon("utar_logo.png"))
        
        self.scanner = WBLEScanner()
        self.scan_task = None
        
        self.init_ui()
        self.init_tray()
        self.apply_macos_dark_theme()
        
        # Redirect stdout to the log widget
        self.log_stream = LogStream()
        self.log_stream.textWritten.connect(self.append_log)
        sys.stdout = self.log_stream
        
        print("🚀 [System] WBLE Agent initialized. Ready to start!")
        
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)
        
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)
        
        # === Left Panel: Courses ===
        self.left_panel_widget = QWidget()
        left_panel = QVBoxLayout(self.left_panel_widget)
        left_panel.setContentsMargins(0, 0, 0, 0)
        left_panel.setSpacing(10)
        
        lbl_courses = QLabel("Courses Monitored\n(Double-click to open)")
        lbl_courses.setStyleSheet("font-size: 14px; font-weight: 600; color: #1d1d1f;")
        left_panel.addWidget(lbl_courses)
        
        self.course_list = QListWidget()
        
        self.course_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.course_list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.course_list.setStyleSheet("""
            QListWidget {
                border: none;
                background-color: transparent;
                outline: none; /* 去除原生虚线框 */
            }
            QListWidget::item {
                border-radius: 8px;
                margin: 2px 8px; /* 卡片两边留白 */
            }
            QListWidget::item:selected {
                background-color: #E5E5EA; /* 类似 Mac 的柔和灰背景 */
                color: #1d1d1f;
            }
            QListWidget::item:hover {
                background-color: #F2F2F7;
            }
        """)
        
        self.refresh_course_list()
        self.course_list.itemDoubleClicked.connect(self.open_course_folder)
        left_panel.addWidget(self.course_list)
        
        btn_remove_course = QPushButton("Remove Selected")
        btn_remove_course.clicked.connect(self.remove_selected_course)
        left_panel.addWidget(btn_remove_course)
        
        self.splitter.addWidget(self.left_panel_widget)
        
        # === Center Panel: Console ===
        self.center_panel_widget = QWidget()
        center_panel = QVBoxLayout(self.center_panel_widget)
        center_panel.setContentsMargins(0, 0, 0, 0)
        center_panel.setSpacing(10)
        
        # Top bar of center panel (contains collapse button and title)
        center_top_layout = QHBoxLayout()
        
        # 类似 Teams 的收起/展开面板按钮
        self.btn_toggle_sidebar = QToolButton()
        # 创建一个 SVG 图标
        sidebar_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#515154" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
            <line x1="9" y1="3" x2="9" y2="21"></line>
        </svg>"""
        import tempfile
        svg_path = os.path.join(tempfile.gettempdir(), "sidebar_icon.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(sidebar_svg)
        
        self.btn_toggle_sidebar.setIcon(QIcon(svg_path))
        self.btn_toggle_sidebar.setIconSize(QSize(20, 20))
        self.btn_toggle_sidebar.setToolTip("Toggle Sidebar")
        self.btn_toggle_sidebar.setStyleSheet("QToolButton { border: none; padding: 4px; border-radius: 6px; } QToolButton:hover { background-color: #e5e5ea; }")
        self.btn_toggle_sidebar.clicked.connect(self.toggle_sidebar)
        
        lbl_console = QLabel("Activity Log")
        lbl_console.setStyleSheet("font-size: 14px; font-weight: 600; color: #1d1d1f;")
        
        # Right Sidebar Toggle Button
        self.btn_toggle_right = QToolButton()
        right_sidebar_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#515154" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
            <line x1="15" y1="3" x2="15" y2="21"></line>
        </svg>"""
        svg_path_right = os.path.join(tempfile.gettempdir(), "right_sidebar_icon.svg")
        with open(svg_path_right, "w", encoding="utf-8") as f:
            f.write(right_sidebar_svg)
            
        self.btn_toggle_right.setIcon(QIcon(svg_path_right))
        self.btn_toggle_right.setIconSize(QSize(20, 20))
        self.btn_toggle_right.setToolTip("Toggle Settings")
        self.btn_toggle_right.setStyleSheet("QToolButton { border: none; padding: 4px; border-radius: 6px; } QToolButton:hover { background-color: #e5e5ea; }")
        self.btn_toggle_right.clicked.connect(self.toggle_right_sidebar)
        
        center_top_layout.addWidget(self.btn_toggle_sidebar)
        center_top_layout.addWidget(lbl_console)
        center_top_layout.addStretch()
        center_top_layout.addWidget(self.btn_toggle_right)
        
        center_panel.addLayout(center_top_layout)
        
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        # Clean white terminal with soft gray text
        self.console.setStyleSheet("background-color: #f5f5f7; color: #515154; font-family: 'SF Pro Text', 'Segoe UI', Consolas; font-size: 13px; border-radius: 12px; padding: 10px; border: 1px solid #e5e5ea;")
        center_panel.addWidget(self.console, stretch=1)
        
        btn_scan = QPushButton("Force Scan Now")
        btn_scan.setMinimumHeight(44)
        btn_scan.setStyleSheet("background-color: #007aff; color: white; font-weight: 600; border-radius: 12px; font-size: 14px;")
        btn_scan.clicked.connect(self.force_scan)
        center_panel.addWidget(btn_scan)
        
        self.splitter.addWidget(self.center_panel_widget)
        
        # === Right Panel: Settings ===
        self.right_panel_widget = QWidget()
        right_panel = QVBoxLayout(self.right_panel_widget)
        right_panel.setContentsMargins(0, 0, 0, 0)
        right_panel.setSpacing(12)
        # Header Layout
        header_layout = QHBoxLayout()
        title_vlayout = QVBoxLayout()
        
        # Title
        lbl_title = QLabel("WBLE Course Agent")
        lbl_title.setStyleSheet("font-size: 26px; font-weight: 800; color: #1d1d1f;")
        lbl_subtitle = QLabel("Automated learning environment sync & summarization")
        lbl_subtitle.setStyleSheet("font-size: 13px; color: #86868b; margin-bottom: 20px;")
        
        title_vlayout.addWidget(lbl_title)
        title_vlayout.addWidget(lbl_subtitle)
        header_layout.addLayout(title_vlayout)
        header_layout.addStretch()
        
        btn_overview = self.create_help_btn(
            "WBLE Agent 总览指南",
            "欢迎使用 WBLE Agent (极简特供版)！🚀\n\n"
            "这是你的全自动化课件管家，核心功能如下：\n\n"
            "1. 📥 无感同步: 自动登录 WBLE 并在后台深潜抓取所有最新的课件、实验指导和考试资料，分门别类存入你指定的文件夹。\n"
            "2. 🧠 AI 智能总结: 每次网页有新动态（比如老师发了新公告、布置了作业），自动调用大模型为你生成精简的 Markdown 笔记。\n"
            "3. 📱 微信实时推送: 如果有重要更新，它会通过 Server酱 瞬间推送到你的手机微信。\n"
            "4. 🛡️ 智能防爆: 自动识别和过滤（可通过 Max File Limit 拦截）过大的无用文件或教学录像。\n\n"
            "💡 最佳实践: \n"
            "配好左侧的任何一个大模型 API 密钥并勾选 [Silent Startup] (开机自启)。它就会像一个幽灵管家，默默潜伏在系统托盘，保你这学期课件一字不落！"
        )
        btn_overview.setFixedSize(30, 30) # Make the main overview button slightly larger
        header_layout.addWidget(btn_overview)
        
        right_panel.addLayout(header_layout)
        
        # Download Path
        lbl_path = QLabel("Download Location")
        lbl_path.setStyleSheet("color: #86868b; font-size: 12px;")
        right_panel.addWidget(lbl_path)
        path_layout = QHBoxLayout()
        self.in_path = QLineEdit(config_mgr.get("download_dir"))
        self.in_path.setReadOnly(True)
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self.browse_path)
        path_layout.addWidget(self.in_path)
        path_layout.addWidget(btn_browse)
        right_panel.addLayout(path_layout)
        
        # API Keys Section
        lbl_api_section = QLabel("AI Engines (必填: 下列至少选一)")
        lbl_api_section.setStyleSheet("font-size: 14px; font-weight: 600; color: #1d1d1f; margin-top: 15px;")
        right_panel.addWidget(lbl_api_section)
        
        lbl_github = QLabel("GitHub GPT-4o Token (Free)")
        lbl_github.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 5px; font-weight: bold;")
        right_panel.addWidget(lbl_github)
        github_layout = QHBoxLayout()
        github_layout.setSpacing(6)
        self.in_openai = QLineEdit(config_mgr.get("api_keys", {}).get("openai", ""))
        self.in_openai.setEchoMode(QLineEdit.EchoMode.Password)
        btn_get_github = QPushButton("Get Key")
        btn_get_github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/marketplace/models/azure-openai/gpt-4o")))
        btn_help_github = self.create_help_btn(
            "GitHub 获取教程与规则", 
            "教程: 点击 [Get Key] 登录 -> 右上角点击 [Use this model] -> 选择 [Get a Personal Access Token] -> 滑到最底点击 [Generate token]。\n\n"
            "💡 免费规则: GitHub 官方提供免费的 GPT-4o 额度，通常为每分钟 15 次调用，每天最多 150 次。系统每天会自动刷新额度，绝对足够日常的课件总结使用，纯白嫖无负担！\n\n"
            "🔑 最后，复制生成的以 github_pat_ 或 ghp_ 开头的密钥粘贴于此。"
        )
        github_layout.addWidget(self.in_openai)
        github_layout.addWidget(btn_get_github)
        github_layout.addWidget(btn_help_github)
        right_panel.addLayout(github_layout)
        
        lbl_groq = QLabel("Groq API Key (Fast & Free)")
        lbl_groq.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 10px; font-weight: bold;")
        right_panel.addWidget(lbl_groq)
        groq_layout = QHBoxLayout()
        groq_layout.setSpacing(6)
        self.in_groq = QLineEdit(config_mgr.get("api_keys", {}).get("groq", ""))
        self.in_groq.setEchoMode(QLineEdit.EchoMode.Password)
        btn_get_groq = QPushButton("Get Key")
        btn_get_groq.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://console.groq.com/keys")))
        btn_help_groq = self.create_help_btn(
            "Groq 获取教程与规则", 
            "教程: 点击 [Get Key] 登录 Groq Console -> 侧边栏进入 [API Keys] -> 点击 [Create API Key]。\n\n"
            "💡 免费规则: Groq 提供非常快速且免费的 Llama 3 接口，极度推荐！\n\n"
            "🔑 最后，复制生成的以 gsk_ 开头的密钥粘贴于此。"
        )
        groq_layout.addWidget(self.in_groq)
        groq_layout.addWidget(btn_get_groq)
        groq_layout.addWidget(btn_help_groq)
        right_panel.addLayout(groq_layout)
        
        lbl_kimi = QLabel("Kimi API Key (Fallback 1)")
        lbl_kimi.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 10px; font-weight: bold;")
        right_panel.addWidget(lbl_kimi)
        kimi_layout = QHBoxLayout()
        kimi_layout.setSpacing(6)
        self.in_kimi = QLineEdit(config_mgr.get("api_keys", {}).get("kimi", ""))
        self.in_kimi.setEchoMode(QLineEdit.EchoMode.Password)
        btn_get_kimi = QPushButton("Get Key")
        btn_get_kimi.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://platform.moonshot.cn/console/api-keys")))
        btn_help_kimi = self.create_help_btn(
            "Kimi 获取教程与规则", 
            "教程: 点击 [Get Key] 登录 Kimi 开放平台 -> 侧边栏进入 [API Key 管理] -> 点击 [新建 API Key]。\n\n"
            "💡 免费规则: 新用户注册 Kimi 开放平台一般会直接赠送 15 元体验金！此外，Kimi 官方平台经常有每天免费认领额度的活动，用完后也不会自动扣款（需主动充值），极其适合作为备用大模型。\n\n"
            "🔑 最后，复制生成的密钥粘贴于此。"
        )
        kimi_layout.addWidget(self.in_kimi)
        kimi_layout.addWidget(btn_get_kimi)
        kimi_layout.addWidget(btn_help_kimi)
        right_panel.addLayout(kimi_layout)
        
        lbl_gemini = QLabel("Google Gemini Key (Fallback 2)")
        lbl_gemini.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 10px; font-weight: bold;")
        right_panel.addWidget(lbl_gemini)
        gemini_layout = QHBoxLayout()
        gemini_layout.setSpacing(6)
        self.in_gemini = QLineEdit(config_mgr.get("api_keys", {}).get("gemini", ""))
        self.in_gemini.setEchoMode(QLineEdit.EchoMode.Password)
        btn_get_gemini = QPushButton("Get Key")
        btn_get_gemini.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://aistudio.google.com/app/apikey")))
        btn_help_gemini = self.create_help_btn(
            "Gemini 获取教程与规则", 
            "教程: 点击 [Get Key] 登录 Google AI Studio -> 点击左侧 [Get API key] -> 点击 [Create API key]。\n\n"
            "💡 免费规则: Google 对所有普通用户免费开放 Gemini Pro，提供每分钟高达 15 次的无门槛调用额度。不需要绑定信用卡，也不需要充值，非常适合高强度的重度白嫖！\n\n"
            "🔑 最后，复制生成的密钥粘贴于此。"
        )
        gemini_layout.addWidget(self.in_gemini)
        gemini_layout.addWidget(btn_get_gemini)
        gemini_layout.addWidget(btn_help_gemini)
        right_panel.addLayout(gemini_layout)
        
        # ServerChan (WeChat) Key
        lbl_wechat = QLabel("Server酱 WeChat Push Key (Optional)")
        lbl_wechat.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 10px; font-weight: bold;")
        right_panel.addWidget(lbl_wechat)
        wechat_layout = QHBoxLayout()
        wechat_layout.setSpacing(6)
        self.in_wechat = QLineEdit(config_mgr.get("serverchan_key", ""))
        self.in_wechat.setEchoMode(QLineEdit.EchoMode.Password)
        btn_get_wechat = QPushButton("Get Key")
        btn_get_wechat.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://sct.ftqq.com/sendkey")))
        btn_help_wechat = self.create_help_btn(
            "微信推送设置与获取教程", 
            "教程: 点击 [Get Key] 登录 Server酱 (sct.ftqq.com) -> 使用微信扫码登录 -> 进入顶部的 [SendKey] 菜单栏 -> 复制那一串 SendKey 粘贴于此。\n\n"
            "💡 免费规则: 每天提供 5 条免费推送额度，用来接收重要课件更新通知绝对够用。\n\n"
            "🔑 注意: 这是一个【选填项】！如果不填，软件一样会在后台默默下载课件并写好 Markdown 笔记存进文件夹，只是不会发微信通知给你而已。"
        )
        wechat_layout.addWidget(self.in_wechat)
        wechat_layout.addWidget(btn_get_wechat)
        wechat_layout.addWidget(btn_help_wechat)
        right_panel.addLayout(wechat_layout)
        
        # Interval
        lbl_interval = QLabel("Scan Frequency")
        lbl_interval.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 10px; font-weight: bold;")
        right_panel.addWidget(lbl_interval)
        self.cb_interval = QComboBox()
        self.cb_interval.addItems(["Every 1 hour", "Every 4 hours", "Daily"])
        right_panel.addWidget(self.cb_interval)
        
        # File Size Limit
        lbl_file_limit = QLabel("Max File Limit (MB)")
        lbl_file_limit.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 10px; font-weight: bold;")
        right_panel.addWidget(lbl_file_limit)
        limit_layout = QHBoxLayout()
        limit_layout.setSpacing(6)
        self.in_file_limit = QLineEdit(str(config_mgr.get("max_file_size_mb", 50)))
        btn_help_limit = self.create_help_btn(
            "单文件大小上限规则", 
            "设定单份课件的下载体积上限（默认 50 MB）。\n\n"
            "💡 超过此大小的文件（如大型教学录像、巨型压缩包）将被直接跳过，防止占用你过多硬盘空间或造成网络卡顿、软件崩溃。"
        )
        limit_layout.addWidget(self.in_file_limit)
        limit_layout.addWidget(btn_help_limit)
        right_panel.addLayout(limit_layout)
        
        # Auto Start
        self.chk_autostart = QCheckBox("Silent Startup (System Tray)")
        self.chk_autostart.setStyleSheet("color: #1d1d1f; font-size: 13px; margin-top: 15px;")
        self.chk_autostart.setChecked(config_mgr.get("auto_start", False))
        right_panel.addWidget(self.chk_autostart)
        
        # Save Button
        btn_save = QPushButton("Save Preferences")
        btn_save.setMinimumHeight(44)
        btn_save.setStyleSheet("background-color: #f5f5f7; color: #1d1d1f; border: 1px solid #d1d1d6; font-weight: 500; border-radius: 12px;")
        btn_save.clicked.connect(self.save_settings)
        right_panel.addStretch()
        right_panel.addWidget(btn_save)
        
        self.splitter.addWidget(self.right_panel_widget)
        
        # Style the splitter handle to be subtle
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: transparent;
                margin: 0px 5px;
            }
        """)
        
        # Set initial splitter sizes
        self.splitter.setSizes([250, 500, 300])
        
        self.toast = ToastNotification(self)

    def toggle_sidebar(self):
        # Teams style collapse/expand using QPropertyAnimation
        self.anim = QPropertyAnimation(self.left_panel_widget, b"maximumWidth")
        self.anim.setDuration(250)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        if self.left_panel_widget.maximumWidth() == 0:
            # Expand
            self.anim.setStartValue(0)
            self.anim.setEndValue(350)
            self.left_panel_widget.setMinimumWidth(150)
        else:
            # Collapse
            self.left_panel_widget.setMinimumWidth(0)
            self.anim.setStartValue(self.left_panel_widget.width())
            self.anim.setEndValue(0)
            
        self.anim.start()

    def toggle_right_sidebar(self):
        # Settings collapse/expand using QPropertyAnimation
        self.anim_right = QPropertyAnimation(self.right_panel_widget, b"maximumWidth")
        self.anim_right.setDuration(250)
        self.anim_right.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        if self.right_panel_widget.maximumWidth() == 0:
            # Expand
            self.anim_right.setStartValue(0)
            self.anim_right.setEndValue(400)
            self.right_panel_widget.setMinimumWidth(250)
        else:
            # Collapse
            self.right_panel_widget.setMinimumWidth(0)
            self.anim_right.setStartValue(self.right_panel_widget.width())
            self.anim_right.setEndValue(0)
            
        self.anim_right.start()

    def create_help_btn(self, title, text):
        btn = QPushButton("i")
        btn.setFixedSize(24, 24)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                border-radius: 12px;
                background-color: #e5e5ea;
                color: #515154;
                font-family: "Segoe UI", Arial, sans-serif;
                font-weight: bold;
                font-size: 13px;
                border: none;
                padding: 0px;
                margin: 0px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #d1d1d6;
                color: #1d1d1f;
            }
        """)
        btn.clicked.connect(lambda: QMessageBox.information(self, title, text))
        return btn

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("utar_logo.png"))
        
        tray_menu = QMenu()
        show_action = QAction("打开主面板", self)
        show_action.triggered.connect(self.showNormal)
        
        scan_action = QAction("立即强制扫描", self)
        scan_action.triggered.connect(self.force_scan)
        
        quit_action = QAction("完全退出", self)
        quit_action.triggered.connect(sys.exit)
        
        tray_menu.addAction(show_action)
        tray_menu.addAction(scan_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        
    def refresh_course_list(self):
        self.course_list.clear()
        available = config_mgr.get("available_courses", [])
        blacklist = config_mgr.get("blacklisted_courses", [])
        
        # 加载最新的 state_db 获取状态
        states = config_mgr.state
        
        for course in available:
            if course not in blacklist:
                # Custom widget for list item
                item_widget = QWidget()
                h_layout = QHBoxLayout(item_widget)
                h_layout.setContentsMargins(10, 6, 10, 6)
                h_layout.setSpacing(12)
                
                # 使用自定义的省略号 Label，保证所有课程名只占一行且宽度自适应
                lbl_name = ElidedLabel(course)
                lbl_name.setStyleSheet("font-size: 13px; font-weight: 600; color: #1d1d1f; background: transparent;")
                lbl_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                lbl_name.setMinimumWidth(40) # 允许极限缩小
                h_layout.addWidget(lbl_name, stretch=1)
                
                course_state = states.get(course, {})
                md_ok = course_state.get("md_generated", False)
                ics_ok = course_state.get("ics_generated", False)
                
                # 状态栏 (垂直排列)
                status_layout = QVBoxLayout()
                status_layout.setSpacing(4)
                status_layout.setContentsMargins(0, 4, 0, 4)
                
                def create_badge(text, is_success):
                    lbl = QLabel(text)
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lbl.setFixedSize(36, 18)
                    if is_success:
                        lbl.setStyleSheet("background-color: #E6F4EA; color: #137333; border-radius: 9px; font-size: 10px; font-weight: 700;")
                    else:
                        lbl.setStyleSheet("background-color: #FCE8E6; color: #C5221F; border-radius: 9px; font-size: 10px; font-weight: 700;")
                    return lbl
                
                lbl_md = create_badge("MD", md_ok)
                lbl_ics = create_badge("ICS", ics_ok)
                
                status_layout.addWidget(lbl_md)
                status_layout.addWidget(lbl_ics)
                
                h_layout.addLayout(status_layout, stretch=0) # 坚守宽度，不被挤压
                
                # Create QListWidgetItem
                list_item = QListWidgetItem(self.course_list)
                # 强制给定一个精确高度，完美包裹内部布局，解决框线越界问题
                list_item.setSizeHint(QSize(self.course_list.width(), 62))
                list_item.setData(Qt.ItemDataRole.UserRole, course) # Store actual course name
                
                self.course_list.addItem(list_item)
                self.course_list.setItemWidget(list_item, item_widget)
                
    def remove_selected_course(self):
        item = self.course_list.currentItem()
        if item:
            course = item.data(Qt.ItemDataRole.UserRole) or item.text()
            blacklist = config_mgr.get("blacklisted_courses", [])
            if course not in blacklist:
                blacklist.append(course)
                config_mgr.set("blacklisted_courses", blacklist)
            self.refresh_course_list()
            print(f"🗑️ 已将课程移出监控列表: {course}")

    def open_course_folder(self, item):
        course = item.data(Qt.ItemDataRole.UserRole) or item.text()
        safe_course_name = re.sub(r'[\\/*?:"<>|]', "_", course)
        base_dir = config_mgr.get("download_dir", os.path.join(os.getcwd(), "WBLE_Downloads"))
        course_dir = os.path.join(base_dir, safe_course_name)
        if os.path.exists(course_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(course_dir))
            print(f"📂 已在资源管理器中打开: {course}")
        else:
            QMessageBox.information(self, "文件夹不存在", "该课程的文件夹尚未建立，可能是还没下载过任何文件。")

    def browse_path(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择下载保存路径")
        if dir_path:
            self.in_path.setText(dir_path)

    def save_settings(self):
        openai_key = self.in_openai.text().strip()
        groq_key = self.in_groq.text().strip()
        kimi_key = self.in_kimi.text().strip()
        gemini_key = self.in_gemini.text().strip()
        
        # Validation
        if not (openai_key or groq_key or kimi_key or gemini_key):
            QMessageBox.warning(self, "缺少 API 密钥", "【必填】请在右侧至少填写一个 AI 引擎的 API 密钥 (GitHub / Groq / Kimi / Gemini)！\n否则软件无法为你自动总结课件。")
            return False
            
        if openai_key and not (openai_key.startswith("github_pat_") or openai_key.startswith("ghp_")):
            QMessageBox.warning(self, "格式错误", "GitHub Token 格式似乎不对！\n正常应该以 github_pat_ 或 ghp_ 开头，请参考旁边的 'i' 教程重新获取。")
            return False
            
        if kimi_key and not kimi_key.startswith("sk-"):
            QMessageBox.warning(self, "格式错误", "Kimi API Key 格式似乎不对！\n正常应该以 sk- 开头，请参考教程重新获取。")
            return False
            
        if groq_key and not groq_key.startswith("gsk_"):
            QMessageBox.warning(self, "格式错误", "Groq API Key 格式似乎不对！\n正常应该以 gsk_ 开头，请参考教程重新获取。")
            return False
            
        if gemini_key and not (gemini_key.startswith("AIza") or gemini_key.startswith("AQ")):
            QMessageBox.warning(self, "格式错误", "Gemini API Key 格式似乎不对！\n正常应该以 AIza 或 AQ 开头，请参考教程重新获取。")
            return False

        config_mgr.set("download_dir", self.in_path.text())
        keys = config_mgr.get("api_keys", {})
        keys["openai"] = openai_key
        keys["groq"] = groq_key
        keys["kimi"] = kimi_key
        keys["gemini"] = gemini_key
        config_mgr.set("api_keys", keys)
        
        config_mgr.set("serverchan_key", self.in_wechat.text().strip())
        
        try:
            limit = int(self.in_file_limit.text().strip())
            config_mgr.set("max_file_size_mb", limit)
        except ValueError:
            pass
            
        config_mgr.set("auto_start", self.chk_autostart.isChecked())
        config_mgr.set("setup_completed", True)
        self.preferences_saved_this_session = True
        print("💾 配置已成功保存！")
        return True
        
    def append_log(self, text):
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)
        self.console.insertPlainText(text)
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)
        
    def force_scan(self):
        keys = config_mgr.get("api_keys", {})
        if not (keys.get("openai") or keys.get("groq") or keys.get("kimi") or keys.get("gemini")):
            QMessageBox.warning(self, "Action Required", "请先配置并保存至少一个 AI 引擎的 API 密钥后，再启动扫描。")
            return

        if not config_mgr.get("setup_completed", False) and not getattr(self, 'preferences_saved_this_session', False):
            QMessageBox.warning(self, "Action Required", "Please click 'Save Preferences' to confirm your settings before running the scan.")
            return
            
        if not config_mgr.get("first_run_confirmed", False):
            current_path = config_mgr.get("download_dir")
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Confirm Download Path")
            msg_box.setText(f"Your files will be downloaded to:\n\n{current_path}\n\nIs this correct?")
            from PyQt6.QtGui import QPixmap
            pixmap = QPixmap("utar_logo.png").scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            msg_box.setIconPixmap(pixmap)
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg_box.setDefaultButton(QMessageBox.StandardButton.Yes)
            
            reply = msg_box.exec()
            if reply == QMessageBox.StandardButton.Yes:
                config_mgr.set("first_run_confirmed", True)
            else:
                print("⚠️ 用户取消了扫描以重新配置下载路径。")
                return

        if self.scan_task and not self.scan_task.done():
            print("⚠️ 当前正在扫描中，请勿重复点击！")
            return
        print("\n" + "="*40)
        print("🚀 收到手动强制扫描指令，准备启动...")
        self.scan_task = asyncio.create_task(self.run_scan_wrapper())
        
    async def run_scan_wrapper(self):
        try:
            await self.scanner.init_browser()
            logged_in = await self.scanner.wait_for_login()
            if logged_in:
                updates = await self.scanner.run_scan_cycle()
                self.refresh_course_list()
                if updates:
                    self.tray_icon.showMessage("WBLE 有新动态！", f"发现 {len(updates)} 门课有更新，课件已下载！", QSystemTrayIcon.MessageIcon.Information, 5000)
                # Teams style floating toast notification
                self.toast.show_toast("🎉 WBLE 环境扫描完毕！")
        except Exception as e:
            print(f"❌ 扫描过程中发生错误: {e}")
        finally:
            await self.scanner.cleanup()
            print("🛑 浏览器引擎已安全释放。")

    def apply_macos_dark_theme(self):
        # Premium Minimalist White Light Mode (macOS Big Sur+ inspired)
        qss = """
        QMainWindow, QDialog, QMessageBox {
            background-color: #f5f5f7;
        }
        QWidget {
            color: #1d1d1f;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        QLineEdit, QComboBox, QListWidget {
            background-color: #ffffff;
            color: #1d1d1f;
            border: 1px solid #d1d1d6;
            border-radius: 8px;
            padding: 8px 12px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 13px;
        }
        QComboBox QAbstractItemView {
            background-color: #ffffff;
            color: #1d1d1f;
            border: 1px solid #d1d1d6;
            border-radius: 8px;
            selection-background-color: #f5f5f7;
            selection-color: #1d1d1f;
            outline: none;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
            background: transparent;
        }
        QListWidget {
            border-radius: 12px;
            padding: 4px;
        }
        QListWidget::item {
            padding: 8px;
            border-radius: 6px;
        }
        QListWidget::item:selected {
            background-color: #f5f5f7;
            color: #1d1d1f;
        }
        QPushButton {
            background-color: #f5f5f7;
            color: #1d1d1f;
            border: 1px solid #e5e5ea;
            border-radius: 8px;
            padding: 6px 14px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 13px;
        }
        QPushButton:hover {
            background-color: #e5e5ea;
        }
        QPushButton:pressed {
            background-color: #d1d1d6;
        }
        QCheckBox {
            color: #1d1d1f;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            spacing: 8px;
        }
        QCheckBox::indicator {
            width: 18px;
            height: 18px;
            border-radius: 4px;
            border: 1px solid #d1d1d6;
            background-color: #ffffff;
        }
        QCheckBox::indicator:checked {
            background-color: #007aff;
            border: 1px solid #007aff;
        }
        QScrollBar:vertical {
            border: none;
            background: transparent;
            width: 8px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:vertical {
            background: #d1d1d6;
            min-height: 30px;
            border-radius: 4px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            border: none;
            background: none;
        }
        """
        self.setStyleSheet(qss)
