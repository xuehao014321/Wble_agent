import sys
import os
import re
import asyncio
import logging
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QLabel, QLineEdit, QTextEdit, QFileDialog, QCheckBox, 
    QSystemTrayIcon, QMenu, QListWidget, QListWidgetItem, QSlider, QComboBox,
    QMessageBox, QSplitter, QGraphicsOpacityEffect, QSizePolicy, QToolButton,
    QAbstractItemView, QApplication, QScrollArea, QFrame
)
from PyQt6.QtGui import (
    QIcon, QDesktopServices, QAction, QColor, QPalette, QPainter,
    QPainterPath, QMovie, QPen
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QObject, QUrl, QPropertyAnimation, QEasingCurve,
    QTimer, QSize, QPoint, QRect
)

from core.config import config_mgr
from core.engine import (
    TARGETS_VERSION,
    WBLEScanner,
    get_dashboard_targets,
    remove_target_auth_state,
)
from core.autostart import is_autostart_enabled, set_autostart_enabled
from core.filesystem import move_to_recycle_bin


def resource_path(filename):
    """Return an asset path that works from source and a PyInstaller bundle."""
    base_dir = getattr(
        sys,
        "_MEIPASS",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(base_dir, filename)


def is_github_classic_token(value):
    """Accept only GitHub personal access tokens (classic)."""
    return str(value or "").strip().startswith("ghp_")


class LogStream(QObject):
    textWritten = pyqtSignal(str)

    def write(self, text):
        text = str(text)
        self.textWritten.emit(text)
        if text.strip():
            logging.getLogger("wble.console").info(text.rstrip())
        
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
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        
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
        
        self.raise_()
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


class ScanSuccessAnimation(QWidget):
    def __init__(self, animation_path, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("background: transparent;")
        layout.addWidget(self.label)

        self.movie = QMovie(animation_path)
        self.movie.setCacheMode(QMovie.CacheMode.CacheNone)
        self.label.setMovie(self.movie)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.stop_and_hide)
        self.hide()

    def play(self):
        if not self.movie.isValid():
            return False

        self.movie.stop()
        self.movie.jumpToFrame(0)
        frame_size = self.movie.currentPixmap().size()
        if frame_size.isValid():
            self.setFixedSize(frame_size)

        if self.parent():
            parent_rect = self.parent().rect()
            self.move(
                parent_rect.center().x() - self.width() // 2,
                parent_rect.center().y() - self.height() // 2
            )

        self.raise_()
        self.show()
        self.movie.start()
        self.hide_timer.start(3000)
        return True

    def stop_and_hide(self):
        self.movie.stop()
        self.hide()


class SpotlightGuide(QWidget):
    """Modal coach marks that spotlight one main-window control at a time."""

    def __init__(self, parent, steps):
        super().__init__(parent)
        self.steps = steps
        self.step_index = 0
        self.highlight_rect = QRect()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.card = QFrame(self)
        self.card.setObjectName("spotlightGuideCard")
        self.card.setStyleSheet("""
            QFrame#spotlightGuideCard {
                background: white;
                border: 1px solid rgba(255, 255, 255, 0.75);
                border-radius: 18px;
            }
            QLabel#spotlightGuideTitle {
                color: #172033;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#spotlightGuideBody {
                color: #526078;
                font-size: 14px;
                line-height: 1.5;
            }
            QLabel#spotlightGuideCounter {
                color: #8a94a6;
                font-size: 12px;
            }
            QPushButton {
                min-height: 34px;
                padding: 0 14px;
                border-radius: 9px;
            }
            QPushButton#spotlightGuideNext {
                background: #007aff;
                color: white;
                border: none;
                font-weight: 600;
            }
        """)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(22, 20, 22, 18)
        card_layout.setSpacing(10)

        self.counter_label = QLabel()
        self.counter_label.setObjectName("spotlightGuideCounter")
        self.title_label = QLabel()
        self.title_label.setObjectName("spotlightGuideTitle")
        self.body_label = QLabel()
        self.body_label.setObjectName("spotlightGuideBody")
        self.body_label.setWordWrap(True)

        card_layout.addWidget(self.counter_label)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.body_label)

        controls = QHBoxLayout()
        self.skip_button = QPushButton("跳过导览")
        self.back_button = QPushButton("上一步")
        self.next_button = QPushButton("下一步")
        self.next_button.setObjectName("spotlightGuideNext")
        self.skip_button.clicked.connect(self.finish)
        self.back_button.clicked.connect(self.previous_step)
        self.next_button.clicked.connect(self.next_step)
        controls.addWidget(self.skip_button)
        controls.addStretch()
        controls.addWidget(self.back_button)
        controls.addWidget(self.next_button)
        card_layout.addLayout(controls)

        self.card.setFixedWidth(390)
        self.hide()

    def start(self):
        self.step_index = 0
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.setFocus()
        self.show_step()

    def show_step(self):
        step = self.steps[self.step_index]
        target = step["target"]
        parent = self.parentWidget()

        if (
            parent.right_panel_widget.isAncestorOf(target)
            and parent.right_panel_widget.maximumWidth() == 0
        ):
            parent.right_panel_widget.setMinimumWidth(250)
            parent.right_panel_widget.setMaximumWidth(400)
        if (
            (
                target is parent.left_panel_widget
                or parent.left_panel_widget.isAncestorOf(target)
            )
            and parent.left_panel_widget.maximumWidth() == 0
        ):
            parent.left_panel_widget.setMinimumWidth(150)
            parent.left_panel_widget.setMaximumWidth(350)

        if parent.right_panel_widget.isAncestorOf(target):
            parent.right_panel_widget.ensureWidgetVisible(
                target, 24, 80
            )

        self.counter_label.setText(
            f"新用户操作指南  ·  {self.step_index + 1}/{len(self.steps)}"
        )
        self.title_label.setText(step["title"])
        self.body_label.setText(step["body"])
        self.back_button.setVisible(self.step_index > 0)
        self.next_button.setText(
            "完成" if self.step_index == len(self.steps) - 1 else "下一步"
        )
        self.card.adjustSize()
        self.card.setFixedWidth(390)
        QTimer.singleShot(60, self.refresh_geometry)

    def refresh_geometry(self):
        if not self.isVisible():
            return
        self.setGeometry(self.parentWidget().rect())
        target = self.steps[self.step_index]["target"]
        # The target can live inside QScrollArea while this overlay is a
        # sibling on the main window. Mapping directly between siblings gives
        # incorrect coordinates on Windows/high-DPI displays, especially
        # after the settings panel scrolls. Global coordinates provide a
        # stable bridge between the two widget hierarchies.
        top_left = self.mapFromGlobal(
            target.mapToGlobal(QPoint(0, 0))
        )
        self.highlight_rect = QRect(
            top_left, target.size()
        ).adjusted(-9, -9, 9, 9)

        margin = 20
        card_width = self.card.width()
        card_height = self.card.sizeHint().height()
        if self.highlight_rect.center().x() > self.width() // 2:
            card_x = self.highlight_rect.left() - card_width - margin
        else:
            card_x = self.highlight_rect.right() + margin
        card_x = max(margin, min(card_x, self.width() - card_width - margin))
        card_y = self.highlight_rect.center().y() - card_height // 2
        card_y = max(margin, min(card_y, self.height() - card_height - margin))
        self.card.setGeometry(card_x, card_y, card_width, card_height)
        self.card.raise_()
        self.update()

    def next_step(self):
        if self.step_index >= len(self.steps) - 1:
            self.finish()
            return
        self.step_index += 1
        self.show_step()

    def previous_step(self):
        if self.step_index == 0:
            return
        self.step_index -= 1
        self.show_step()

    def finish(self):
        config_mgr.set("onboarding_completed", True)
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.refresh_geometry)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.finish()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.next_step()
        elif event.key() == Qt.Key.Key_Left:
            self.previous_step()
        elif event.key() == Qt.Key.Key_Right:
            self.next_step()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hole = self.highlight_rect.intersected(self.rect())
        shade = QColor(11, 18, 32, 185)
        if not hole.isValid():
            painter.fillRect(self.rect(), shade)
            return

        # Paint four independent panels around the spotlight instead of
        # subtracting a QPainterPath. Path subtraction is unreliable for
        # translucent child widgets on some Windows/high-DPI combinations.
        painter.fillRect(
            QRect(0, 0, self.width(), max(0, hole.top())),
            shade,
        )
        painter.fillRect(
            QRect(
                0,
                hole.bottom() + 1,
                self.width(),
                max(0, self.height() - hole.bottom() - 1),
            ),
            shade,
        )
        painter.fillRect(
            QRect(
                0,
                hole.top(),
                max(0, hole.left()),
                max(0, hole.height()),
            ),
            shade,
        )
        painter.fillRect(
            QRect(
                hole.right() + 1,
                hole.top(),
                max(0, self.width() - hole.right() - 1),
                max(0, hole.height()),
            ),
            shade,
        )

        painter.setPen(QPen(QColor("#007aff"), 3))
        painter.drawRoundedRect(hole, 14, 14)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UTAR WBLE Agent")
        self.resize(1000, 650)
        self.setWindowIcon(QIcon(resource_path("utar_logo.png")))
        
        self.scanner = WBLEScanner()
        self.scan_task = None
        self.is_quitting = False
        self.close_notice_shown = False
        self.onboarding_checked = False
        self.spotlight_guide = None
        
        self.init_ui()
        self.init_tray()
        self.apply_macos_dark_theme()
        
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.auto_scan_trigger)
        self.update_timer_interval()

        # Redirect stdout to the log widget
        self.log_stream = LogStream()
        self.log_stream.textWritten.connect(self.append_log)
        sys.stdout = self.log_stream
        sys.stderr = self.log_stream
        
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
        self.course_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
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
        
        self.btn_remove_course = QPushButton("Remove Selected")
        self.btn_remove_course.clicked.connect(self.remove_selected_course)
        left_panel.addWidget(self.btn_remove_course)
        
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

        self.btn_user_guide = QPushButton("用户导览")
        self.btn_user_guide.setObjectName("userGuideButton")
        self.btn_user_guide.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_user_guide.setToolTip("重新查看 WBLE Agent 新用户操作指南")
        self.btn_user_guide.setStyleSheet("""
            QPushButton#userGuideButton {
                background: #eef6ff;
                color: #0066cc;
                border: 1px solid #cfe5ff;
                border-radius: 9px;
                padding: 5px 12px;
                font-weight: 600;
            }
            QPushButton#userGuideButton:hover {
                background: #dfeeff;
            }
        """)
        self.btn_user_guide.clicked.connect(
            lambda: self.start_user_guide(show_intro=True)
        )
        
        center_top_layout.addWidget(self.btn_toggle_sidebar)
        center_top_layout.addWidget(lbl_console)
        center_top_layout.addStretch()
        center_top_layout.addWidget(self.btn_user_guide)
        center_top_layout.addWidget(self.btn_toggle_right)
        
        center_panel.addLayout(center_top_layout)
        
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(3000)
        # Clean white terminal with soft gray text
        self.console.setStyleSheet("background-color: #f5f5f7; color: #515154; font-family: 'SF Pro Text', 'Segoe UI', Consolas; font-size: 13px; border-radius: 12px; padding: 10px; border: 1px solid #e5e5ea;")
        center_panel.addWidget(self.console, stretch=1)

        self.btn_scan = QPushButton("Force Scan Now")
        self.btn_scan.setMinimumHeight(44)
        self.btn_scan.setStyleSheet("background-color: #007aff; color: white; font-weight: 600; border-radius: 12px; font-size: 14px;")
        self.btn_scan.clicked.connect(self.force_scan)
        center_panel.addWidget(self.btn_scan)
        
        self.splitter.addWidget(self.center_panel_widget)
        
        # === Right Panel: Settings ===
        self.right_panel_widget = QScrollArea()
        self.right_panel_widget.setWidgetResizable(True)
        self.right_panel_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.right_panel_widget.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.right_panel_widget.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.right_panel_content = QWidget()
        self.right_panel_content.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.right_panel_widget.setWidget(self.right_panel_content)
        right_panel = QVBoxLayout(self.right_panel_content)
        right_panel.setContentsMargins(0, 0, 8, 0)
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
        
        right_panel.addLayout(header_layout)
        
        # Download Path
        lbl_path = QLabel("Download Location")
        lbl_path.setStyleSheet("color: #86868b; font-size: 12px;")
        right_panel.addWidget(lbl_path)
        self.download_path_row = QWidget()
        path_layout = QHBoxLayout(self.download_path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.in_path = QLineEdit(config_mgr.get("download_dir"))
        self.in_path.setReadOnly(True)
        self.btn_browse = QPushButton("Browse")
        self.btn_browse.clicked.connect(self.browse_path)
        path_layout.addWidget(self.in_path)
        path_layout.addWidget(self.btn_browse)
        right_panel.addWidget(self.download_path_row)
        
        # API Keys Section
        self.lbl_api_section = QLabel("AI Engines (必填: 下列至少选一)")
        self.lbl_api_section.setStyleSheet("font-size: 14px; font-weight: 600; color: #1d1d1f; margin-top: 15px;")
        right_panel.addWidget(self.lbl_api_section)
        
        lbl_github = QLabel("GitHub Models Classic Token (Free)")
        lbl_github.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 5px; font-weight: bold;")
        right_panel.addWidget(lbl_github)
        self.github_key_row = QWidget()
        github_layout = QHBoxLayout(self.github_key_row)
        github_layout.setContentsMargins(0, 0, 0, 0)
        github_layout.setSpacing(6)
        self.in_openai = QLineEdit(config_mgr.get("api_keys", {}).get("openai", ""))
        self.in_openai.setEchoMode(QLineEdit.EchoMode.Password)
        self.in_openai.setPlaceholderText("ghp_...")
        btn_get_github = QPushButton("Get Key")
        btn_get_github.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/settings/tokens/new")
            )
        )
        btn_help_github = self.create_help_btn(
            "GitHub Classic Token 获取教程",
            "1. 点击 [Get Key] 并登录 GitHub。\n"
            "2. 确认进入 Settings → Developer settings → "
            "Personal access tokens → Tokens (classic)。\n"
            "3. 点击 [Generate new token (classic)]，填写名称并设置"
            "合理的有效期。\n"
            "4. 仅用于 GitHub Models 时保持最小权限，不要勾选 repo、"
            "admin 等无关的高风险权限。\n"
            "5. 点击 [Generate token]，立即复制生成的 Token。\n\n"
            "🔑 本程序只接受以 ghp_ 开头的 Personal Access Token "
            "(classic)，不接受 github_pat_ 开头的 Fine-grained Token。\n\n"
            "⚠️ Token 与密码同等重要，请勿截图、转发或提交到 GitHub。"
        )
        github_layout.addWidget(self.in_openai)
        github_layout.addWidget(btn_get_github)
        github_layout.addWidget(btn_help_github)
        right_panel.addWidget(self.github_key_row)
        
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
        self.wechat_key_row = QWidget()
        wechat_layout = QHBoxLayout(self.wechat_key_row)
        wechat_layout.setContentsMargins(0, 0, 0, 0)
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
        right_panel.addWidget(self.wechat_key_row)
        
        # Interval
        lbl_interval = QLabel("Scan Frequency")
        lbl_interval.setStyleSheet("color: #86868b; font-size: 12px; margin-top: 10px; font-weight: bold;")
        right_panel.addWidget(lbl_interval)
        self.cb_interval = QComboBox()
        self.cb_interval.addItems(["2 minutes (Test)", "30 minutes", "1 hour", "4 hours", "12 hours"])
        saved_interval = config_mgr.get("scan_interval_str", "1 hour")
        if saved_interval in ["2 minutes (Test)", "30 minutes", "1 hour", "4 hours", "12 hours"]:
            self.cb_interval.setCurrentText(saved_interval)
        right_panel.addWidget(self.cb_interval)
        self.cb_interval.currentTextChanged.connect(self.on_interval_changed)

        # Registered faculty/campus scan targets
        lbl_targets = QLabel("Registered Faculties / Campuses")
        lbl_targets.setStyleSheet(
            "color: #86868b; font-size: 12px; margin-top: 10px; "
            "font-weight: bold;"
        )
        right_panel.addWidget(lbl_targets)
        self.target_list = QListWidget()
        self.target_list.setObjectName("registeredTargetsList")
        self.target_list.setMinimumHeight(88)
        self.target_list.setMaximumHeight(132)
        self.target_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.target_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.target_list.setStyleSheet("""
            QListWidget#registeredTargetsList {
                background: white;
                border: 1px solid #d1d1d6;
                border-radius: 10px;
                padding: 4px;
                outline: none;
            }
            QListWidget#registeredTargetsList::item {
                min-height: 30px;
                padding: 3px 8px;
                margin: 1px 0;
                border-radius: 6px;
            }
            QListWidget#registeredTargetsList::item:selected {
                background: #e8f2ff;
                color: #1d1d1f;
            }
        """)
        self.target_list.setToolTip(
            "每次 Force Scan 选择并登录一个科系，即会自动登记到这里。"
        )
        right_panel.addWidget(self.target_list)
        self.btn_remove_target = QPushButton("Remove Selected Target")
        self.btn_remove_target.clicked.connect(
            self.remove_selected_target
        )
        right_panel.addWidget(self.btn_remove_target)
        self.refresh_dashboard_targets()
        
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
        self.chk_autostart.setChecked(is_autostart_enabled())
        right_panel.addWidget(self.chk_autostart)
        
        # Save Button
        self.btn_save = QPushButton("Save Preferences")
        self.btn_save.setMinimumHeight(44)
        self.btn_save.setStyleSheet("background-color: #f5f5f7; color: #1d1d1f; border: 1px solid #d1d1d6; font-weight: 500; border-radius: 12px;")
        self.btn_save.clicked.connect(self.save_settings)
        right_panel.addStretch()
        right_panel.addWidget(self.btn_save)
        
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
        self.scan_success_animation = ScanSuccessAnimation(
            resource_path("scan_success.webp"),
            self
        )
        self.spotlight_guide = SpotlightGuide(
            self,
            [
                {
                    "target": self.github_key_row,
                    "title": "1. 填写一个 AI Key",
                    "body": (
                        "至少填写 GitHub、Groq、Kimi 或 Gemini 中的一个。"
                        "推荐先使用免费的 GitHub Classic Token；点击右侧 "
                        "Get Key 和 i 可查看获取方法。"
                    ),
                },
                {
                    "target": self.download_path_row,
                    "title": "2. 选择课件保存位置",
                    "body": (
                        "点击 Browse 选择文件夹。扫描到的课件和 AI 生成的 "
                        "Markdown 更新摘要都会整理到这里。"
                    ),
                },
                {
                    "target": self.wechat_key_row,
                    "title": "3. 设置微信更新提醒（可选）",
                    "body": (
                        "填写 Server酱 SendKey 后，发现课程更新时可以收到"
                        "微信提醒。不填写也不影响后台扫描、课件下载和 AI 摘要。"
                        "点击右侧 Get Key 和 i 可查看获取方法。"
                    ),
                },
                {
                    "target": self.cb_interval,
                    "title": "4. 设置后台巡逻频率",
                    "body": (
                        "选择程序多久静默检查一次 WBLE。首次使用建议保留 "
                        "30 minutes。"
                    ),
                },
                {
                    "target": self.chk_autostart,
                    "title": "5. 设置开机自动巡逻",
                    "body": (
                        "勾选 Silent Startup 后，Windows 开机时会在系统托盘"
                        "静默启动 WBLE Agent，并立即进行一次后台扫描；之后"
                        "继续按照你选择的频率巡逻。"
                    ),
                },
                {
                    "target": self.btn_save,
                    "title": "6. 保存设置",
                    "body": (
                        "填写完成后一定要点击 Save Preferences。API Key 会使用 "
                        "Windows 当前账户加密后保存在本机。"
                    ),
                },
                {
                    "target": self.btn_scan,
                    "title": "7. 首次登录并开始扫描",
                    "body": (
                        "点击 Force Scan，在弹出的 Chrome 中选择科系并登录。"
                        "每个需要监控的科系分别执行一次；之后程序就能按设定"
                        "频率在后台静默巡逻。"
                    ),
                },
                {
                    "target": self.left_panel_widget,
                    "title": "8. 查看和管理课程",
                    "body": (
                        "扫描到的课程会显示在左侧。双击课程可打开对应的下载"
                        "文件夹；选择课程后点击 Remove Selected，会在确认后"
                        "停止监控，并把已下载的课程文件夹移入回收站。"
                    ),
                },
            ],
        )

    def showEvent(self, event):
        super().showEvent(event)
        if self.onboarding_checked:
            return
        self.onboarding_checked = True
        should_auto_start = (
            not config_mgr.get("onboarding_completed", False)
            and not config_mgr.get("setup_completed", False)
        )
        if should_auto_start:
            QTimer.singleShot(
                300,
                lambda: self.start_user_guide(show_intro=True),
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.spotlight_guide and self.spotlight_guide.isVisible():
            self.spotlight_guide.setGeometry(self.rect())
            self.spotlight_guide.refresh_geometry()

    def show_program_intro(self):
        intro = QMessageBox(self)
        intro.setWindowTitle("欢迎使用 WBLE Agent")
        intro.setIconPixmap(
            QIcon(resource_path("utar_logo.png")).pixmap(64, 64)
        )
        intro.setText(
            "<b style='font-size:18px'>你的 WBLE 自动课件管家</b>"
        )
        intro.setInformativeText(
            "WBLE Agent 会按设定时间静默巡逻已登录的科系，自动下载"
            "新增课件、比较课程页面变化，并用 AI 生成简洁的 Markdown "
            "更新摘要。<br><br>"
            "<b>首次使用只需完成：</b><br>"
            "① 填写至少一个 AI Key<br>"
            "② 选择下载位置和扫描频率<br>"
            "③ 保存设置<br>"
            "④ 点击 Force Scan，选择科系并完成首次登录<br><br>"
            "登录状态只保存在你的电脑；每个需要监控的科系首次分别"
            "登录一次即可。"
        )
        start_button = intro.addButton(
            "开始设置",
            QMessageBox.ButtonRole.AcceptRole,
        )
        intro.addButton(
            "稍后再说",
            QMessageBox.ButtonRole.RejectRole,
        )
        intro.exec()
        return intro.clickedButton() is start_button

    def start_user_guide(self, show_intro=False):
        if self.scan_task and not self.scan_task.done():
            QMessageBox.information(
                self,
                "扫描正在进行",
                "请等待当前扫描完成后再打开用户导览。",
            )
            return
        if self.spotlight_guide and self.spotlight_guide.isVisible():
            self.spotlight_guide.raise_()
            return
        if show_intro and not self.show_program_intro():
            return
        config_mgr.set("onboarding_completed", True)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(80, self.spotlight_guide.start)

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
        self.tray_icon.setIcon(QIcon(resource_path("utar_logo.png")))
        
        tray_menu = QMenu()
        show_action = QAction("打开主面板", self)
        show_action.triggered.connect(self.show_and_activate)
        
        scan_action = QAction("立即强制扫描", self)
        scan_action.triggered.connect(self.force_scan)
        
        quit_action = QAction("完全退出", self)
        quit_action.triggered.connect(self.request_quit)
        
        tray_menu.addAction(show_action)
        tray_menu.addAction(scan_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()
        
    def on_tray_icon_activated(self, reason):
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_and_activate()

    def show_and_activate(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.is_quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self.close_notice_shown:
            self.tray_icon.showMessage(
                "WBLE Agent 仍在运行",
                "程序已隐藏到系统托盘；使用托盘菜单可完全退出。",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            self.close_notice_shown = True

    def request_quit(self):
        if self.is_quitting:
            return
        self.is_quitting = True
        self.scan_timer.stop()
        asyncio.create_task(self.shutdown_and_quit())

    async def shutdown_and_quit(self):
        if self.scan_task and not self.scan_task.done():
            self.scan_task.cancel()
            try:
                await self.scan_task
            except asyncio.CancelledError:
                pass
        else:
            try:
                await self.scanner.cleanup()
            except Exception as error:
                print(f"⚠️ 退出时浏览器清理失败: {error}")

        self.tray_icon.hide()
        app = QApplication.instance()
        if app:
            app.quit()

    def refresh_dashboard_targets(self):
        if not hasattr(self, "target_list"):
            return
        self.target_list.clear()
        for target in get_dashboard_targets():
            status_icon = {
                "ok": "✅",
                "login_required": "🔐",
                "error": "⚠️",
            }.get(target.get("last_status"), "○")
            item = QListWidgetItem(
                f"{status_icon} {target['label']}"
            )
            details = [target["url"]]
            if target.get("last_checked_at"):
                details.append(
                    f"Last checked: {target['last_checked_at']}"
                )
            if target.get("last_error"):
                details.append(
                    f"Status: {target['last_error']}"
                )
            item.setToolTip("\n".join(details))
            item.setData(Qt.ItemDataRole.UserRole, target)
            item.setSizeHint(QSize(0, 36))
            self.target_list.addItem(item)

    def remove_selected_target(self):
        item = self.target_list.currentItem()
        if not item:
            return
        target = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(target, dict):
            return
        reply = QMessageBox.question(
            self,
            "Remove Scan Target",
            f"停止后台巡逻这个科系/校区吗？\n\n{target['label']}\n"
            "已下载的课程文件和历史状态会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        targets = [
            candidate
            for candidate in get_dashboard_targets()
            if candidate["id"] != target["id"]
        ]
        available = config_mgr.get("available_courses", [])
        available = [
            record for record in available
            if not (
                isinstance(record, dict)
                and record.get("target_id") == target["id"]
            )
        ]
        config_mgr.update({
            "dashboard_targets": targets,
            "dashboard_targets_version": TARGETS_VERSION,
            "dashboard_url": targets[-1]["url"] if targets else "",
            "available_courses": available,
        })
        remove_target_auth_state(target)
        self.refresh_dashboard_targets()
        self.refresh_course_list()
        print(f"✅ 已停止巡逻目标: {target['label']}")
        
    def refresh_course_list(self):
        self.course_list.clear()
        available = config_mgr.get("available_courses", [])
        blacklist = config_mgr.get("blacklisted_courses", [])
        blacklisted_keys = config_mgr.get("blacklisted_course_keys", [])
        
        # 加载最新的 state_db 获取状态
        states = config_mgr.state
        
        for record in available:
            if isinstance(record, dict):
                course_key = record.get("key", "")
                course = record.get("name", course_key)
                target_label = record.get("target_label", "")
            else:
                course_key = str(record)
                course = str(record)
                target_label = ""
                record = {
                    "key": course_key,
                    "name": course,
                    "folder_name": re.sub(r'[\\/*?:"<>|]', "_", course),
                }
            if (
                course not in blacklist
                and course_key not in blacklisted_keys
            ):
                # Custom widget for list item
                item_widget = QWidget()
                h_layout = QHBoxLayout(item_widget)
                h_layout.setContentsMargins(10, 6, 10, 6)
                h_layout.setSpacing(12)
                
                # 使用自定义的省略号 Label，保证所有课程名只占一行且宽度自适应
                display_name = (
                    f"{course} · {target_label}"
                    if target_label else course
                )
                lbl_name = ElidedLabel(display_name)
                lbl_name.setToolTip(display_name)
                lbl_name.setStyleSheet("font-size: 13px; font-weight: 600; color: #1d1d1f; background: transparent;")
                lbl_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                lbl_name.setMinimumWidth(1) # 允许极限缩小
                h_layout.addWidget(lbl_name, stretch=1)
                
                course_state = states.get(
                    course_key, states.get(course, {})
                )
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
                list_item.setSizeHint(QSize(0, 62))
                list_item.setData(
                    Qt.ItemDataRole.UserRole, record
                )
                
                self.course_list.addItem(list_item)
                self.course_list.setItemWidget(list_item, item_widget)
                
    def remove_selected_course(self):
        item = self.course_list.currentItem()
        if item:
            record = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(record, dict):
                course_key = record.get("key", "")
                course = record.get("name", course_key)
                folder_name = record.get("folder_name") or re.sub(
                    r'[\\/*?:"<>|]', "_", course
                )
            else:
                course = str(record or item.text())
                course_key = course
                folder_name = re.sub(r'[\\/*?:"<>|]', "_", course)
            
            # 确认弹窗
            reply = QMessageBox.question(
                self, "确认删除", 
                f"确定要移除课程【{course}】吗？\n\n注意：这将会连同本地已下载的该课件文件夹一起彻底删除，不可恢复！",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                return

            # 将本地文件夹移入回收站，避免误删后无法恢复。
            base_dir = config_mgr.get("download_dir", os.path.join(os.getcwd(), "WBLE_Downloads"))
            base_dir = os.path.abspath(base_dir)
            course_dir = os.path.abspath(
                os.path.join(base_dir, folder_name)
            )

            if (
                course_dir == base_dir
                or os.path.commonpath([base_dir, course_dir]) != base_dir
            ):
                QMessageBox.critical(
                    self,
                    "删除已阻止",
                    "课程文件夹路径校验失败，为保护数据已取消删除。",
                )
                return

            collisions = [
                other
                for other in config_mgr.get("available_courses", [])
                if isinstance(other, dict)
                and other.get("key") != course_key
                and other.get("folder_name") == folder_name
            ]
            if collisions:
                QMessageBox.critical(
                    self,
                    "删除已阻止",
                    "存在名称映射到同一文件夹的其他课程，"
                    "为避免误删已取消操作。",
                )
                return

            if os.path.exists(course_dir):
                try:
                    move_to_recycle_bin(course_dir)
                    print(f"🗑️ 已将课程文件夹移入回收站: {course_dir}")
                except Exception as e:
                    print(f"⚠️ 删除文件夹失败 {course_dir}: {e}")
                    QMessageBox.warning(
                        self,
                        "删除失败",
                        f"课程文件夹未能移入回收站：\n{e}",
                    )
                    return

            blacklisted_keys = config_mgr.get(
                "blacklisted_course_keys", []
            )
            if course_key not in blacklisted_keys:
                blacklisted_keys.append(course_key)
                config_mgr.set(
                    "blacklisted_course_keys", blacklisted_keys
                )

            state_db = config_mgr.state
            if course_key in state_db:
                del state_db[course_key]
                config_mgr.state = state_db
            elif course in state_db:
                # Backward compatibility for a pre-migration course record.
                del state_db[course]
                config_mgr.state = state_db
            config_mgr.save_state()
                    
            self.refresh_course_list()
            print(f"✅ 已将课程移出监控列表: {course}")

    def open_course_folder(self, item):
        record = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(record, dict):
            course = record.get("name", record.get("key", "Course"))
            folder_name = record.get("folder_name") or re.sub(
                r'[\\/*?:"<>|]', "_", course
            )
        else:
            course = str(record or item.text())
            folder_name = re.sub(r'[\\/*?:"<>|]', "_", course)
        base_dir = config_mgr.get("download_dir", os.path.join(os.getcwd(), "WBLE_Downloads"))
        course_dir = os.path.join(base_dir, folder_name)
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

        if openai_key and not is_github_classic_token(openai_key):
            QMessageBox.warning(
                self,
                "格式错误",
                "这里只接受以 ghp_ 开头的 GitHub Personal Access "
                "Token (classic)。\n请点击旁边的 i，按照 Classic Token "
                "教程重新获取。",
            )
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

        keys = dict(config_mgr.get("api_keys", {}))
        keys["openai"] = openai_key
        keys["groq"] = groq_key
        keys["kimi"] = kimi_key
        keys["gemini"] = gemini_key
        
        try:
            limit = int(self.in_file_limit.text().strip())
            if limit <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(
                self,
                "文件大小设置无效",
                "Max File Limit 必须是大于 0 的整数。",
            )
            return False

        auto_start_enabled = self.chk_autostart.isChecked()
        try:
            set_autostart_enabled(auto_start_enabled)
        except Exception as error:
            QMessageBox.warning(
                self,
                "开机自启动设置失败",
                f"Windows 启动项更新失败：\n{error}"
            )
            self.chk_autostart.setChecked(is_autostart_enabled())
            return False

        config_mgr.update({
            "download_dir": self.in_path.text(),
            "api_keys": keys,
            "serverchan_key": self.in_wechat.text().strip(),
            "max_file_size_mb": limit,
            "scan_interval_str": self.cb_interval.currentText(),
            "auto_start": auto_start_enabled,
            "setup_completed": True,
        })
        self.preferences_saved_this_session = True
        print("💾 配置已成功保存！")
        self.update_timer_interval()
        return True
        
    def append_log(self, text):
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)
        self.console.insertPlainText(text)
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)

    def set_scan_ui_locked(self, locked):
        """Prevent settings/course mutations while a scan owns the browser."""
        enabled = not locked
        self.left_panel_widget.setEnabled(enabled)
        self.right_panel_widget.setEnabled(enabled)
        self.btn_scan.setEnabled(enabled)
        if locked:
            self.btn_scan.setText("Scanning…")
            self.btn_scan.setToolTip(
                "扫描期间已锁定设置、课程删除和重复扫描操作。"
            )
        else:
            self.btn_scan.setText("Force Scan Now")
            self.btn_scan.setToolTip("")
        
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
            pixmap = QPixmap(resource_path("utar_logo.png")).scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
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
        self.scan_timer.stop()
        self.set_scan_ui_locked(True)
        self.scan_task = asyncio.create_task(self.run_scan_wrapper(is_background=False))

    def on_interval_changed(self, text):
        config_mgr.set("scan_interval_str", text)
        self.update_timer_interval()

    def update_timer_interval(self):
        interval_str = config_mgr.get("scan_interval_str", "1 hour")
        mapping = {
            "2 minutes (Test)": 2 * 60 * 1000,
            "30 minutes": 30 * 60 * 1000,
            "1 hour": 60 * 60 * 1000,
            "4 hours": 4 * 60 * 60 * 1000,
            "12 hours": 12 * 60 * 60 * 1000
        }
        if interval_str not in mapping:
            interval_str = "1 hour"
            config_mgr.set("scan_interval_str", interval_str)
        ms = mapping[interval_str]
        self.scan_timer.start(ms)
        print(f"⏱️ 后台静默扫描定时器已更新为: {interval_str} ({ms}ms)")

    def auto_scan_trigger(self):
        if self.scan_task and not self.scan_task.done():
            return

        keys = config_mgr.get("api_keys", {})
        if not (keys.get("openai") or keys.get("groq") or keys.get("kimi") or keys.get("gemini")):
            return

        if not config_mgr.get("setup_completed", False):
            return

        print("\n" + "="*40)
        print("👻 [后台模式] 定时任务触发，开始静默巡逻...")
        self.scan_timer.stop()
        self.set_scan_ui_locked(True)
        self.scan_task = asyncio.create_task(self.run_scan_wrapper(is_background=True))

    async def run_scan_wrapper(self, is_background=False):
        try:
            await self.scanner.init_browser(is_background=is_background)
            logged_in = await self.scanner.wait_for_login(is_background=is_background)
            if not logged_in:
                self.refresh_dashboard_targets()
                if is_background:
                    failures = self.scanner.last_scan_report.get(
                        "targets_failed", []
                    )
                    only_login = failures and all(
                        failure.get("reason") == "login_required"
                        for failure in failures
                    )
                    self.tray_icon.showMessage(
                        (
                            "WBLE 登录已过期"
                            if only_login
                            else "WBLE 后台验证失败"
                        ),
                        (
                            "请打开主界面并点击一次 Force Scan "
                            "重新授权所有标记为 🔐 的目标。"
                            if only_login
                            else "请检查网络；标记为 ⚠️ 的目标将在下轮重试。"
                        ),
                        QSystemTrayIcon.MessageIcon.Warning,
                        8000
                    )
                return

            if not is_background:
                # Login/password handling happens in the visible Chrome window.
                # The actual scan is relaunched headlessly so user clicks,
                # address-bar navigation, and page changes cannot corrupt it.
                protected_login = (
                    await self.scanner.switch_to_protected_scan_context()
                )
                if not protected_login:
                    self.tray_icon.showMessage(
                        "WBLE 扫描未启动",
                        "安全扫描窗口未能恢复登录状态，请重新 Force Scan。",
                        QSystemTrayIcon.MessageIcon.Warning,
                        8000,
                    )
                    return

            updates = await self.scanner.run_scan_cycle()
            self.refresh_dashboard_targets()
            self.refresh_course_list()
            report = self.scanner.last_scan_report
            failed_targets = report.get("targets_failed", [])
            if failed_targets:
                failed_names = ", ".join(
                    target.get("label", "WBLE")
                    for target in failed_targets
                )
                self.tray_icon.showMessage(
                    "部分 WBLE 目标扫描失败",
                    f"其他目标已继续完成。需要处理：{failed_names}",
                    QSystemTrayIcon.MessageIcon.Warning,
                    10000,
                )
            if updates:
                self.tray_icon.showMessage("WBLE 有新动态！", f"发现 {len(updates)} 门课有更新，课件已下载！", QSystemTrayIcon.MessageIcon.Information, 5000)
            scan_succeeded = report.get("targets_ok", 0) > 0
            if (
                scan_succeeded
                and not self.scan_success_animation.play()
                and not is_background
            ):
                # 打包遗漏动画资源时，手动扫描仍保留文字提示作为兜底。
                self.toast.show_toast("🎉 WBLE 环境扫描完毕！")
        except Exception as e:
            print(f"❌ 扫描过程中发生错误: {e}")
        finally:
            try:
                await self.scanner.cleanup()
                print("🛑 浏览器引擎已安全释放。")
            except Exception as cleanup_error:
                print(f"⚠️ 浏览器引擎清理失败: {cleanup_error}")
            finally:
                # 无论成功、失败或登录过期，均从本轮结束时重新完整倒计时。
                self.set_scan_ui_locked(False)
                if not self.is_quitting:
                    self.update_timer_interval()

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
