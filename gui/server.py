"""
远程监控服务端 GUI

支持多客户端连接管理、IP 列表、屏幕预览、全屏/关闭等操作。
"""
import sys
import os
import ssl
import socket
import threading
import time

import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QSplitter, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QWidget,
    QStatusBar, QMessageBox, QFrame, QSizePolicy, QAbstractItemView
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QMutex, QMutexLocker, QTimer, QRect
)
from PyQt5.QtGui import (
    QPixmap, QImage, QFont, QIcon, QCloseEvent, QPalette, QColor, QCursor
)


# ============================================================
# 客户端会话线程：接收一个客户端的连续帧
# ============================================================
class ClientSession(QThread):
    """单个客户端的帧接收线程"""

    frame_ready = pyqtSignal(str, bytes)   # ip, jpeg_bytes
    disconnected = pyqtSignal(str)          # ip

    def __init__(self, ssl_sock, addr):
        super().__init__()
        self.sock = ssl_sock
        self.ip = addr[0]
        self.port = addr[1]
        self.running = True

    def run(self):
        buffer = b''
        expected_size = None
        # 15 秒无数据则判定断开（客户端挂起/断网时及时检测）
        self.sock.settimeout(15.0)

        while self.running:
            try:
                # ---- 接收长度头（16 字节） ----
                if expected_size is None:
                    while len(buffer) < 16:
                        chunk = self.sock.recv(1024)
                        if not chunk:
                            raise ConnectionError("连接已断开")
                        buffer += chunk

                    size_str = buffer[:16].decode().strip()
                    expected_size = int(size_str)
                    buffer = buffer[16:]

                    if len(buffer) >= expected_size:
                        img_data = buffer[:expected_size]
                        buffer = buffer[expected_size:]
                        self.frame_ready.emit(self.ip, img_data)
                        expected_size = None
                        continue

                # ---- 接收图片数据 ----
                if expected_size is not None:
                    remaining = expected_size - len(buffer)
                    if remaining > 0:
                        chunk = self.sock.recv(min(4096, remaining))
                        if not chunk:
                            raise ConnectionError("连接已断开")
                        buffer += chunk

                    if len(buffer) >= expected_size:
                        img_data = buffer[:expected_size]
                        buffer = buffer[expected_size:]
                        self.frame_ready.emit(self.ip, img_data)
                        expected_size = None

            except (ssl.SSLError, ConnectionError, OSError, ValueError) as e:
                if self.running:
                    print(f"[-] 客户端 {self.ip} 断开: {e}")
                break
            except Exception as e:
                if self.running:
                    print(f"[-] 客户端 {self.ip} 异常: {e}")
                break

        self.disconnected.emit(self.ip)
        self._cleanup()

    def stop(self):
        self.running = False
        self._cleanup()

    def _cleanup(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ============================================================
# 服务端监听线程
# ============================================================
class ServerListener(QThread):
    """接受新客户端连接，完成 TLS + 认证后派发 ClientSession"""

    new_client = pyqtSignal(object, tuple)   # ssl_socket, addr
    server_error = pyqtSignal(str)

    def __init__(self, host, port, certfile, keyfile, password):
        super().__init__()
        self.host = host
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile
        self.password = password
        self.sock = None
        self.running = False

    def run(self):
        if not os.path.exists(self.certfile) or not os.path.exists(self.keyfile):
            self.server_error.emit(
                f"证书文件缺失，请先生成:\n  python generate_cert.py\n"
                f"需要: {self.certfile}, {self.keyfile}"
            )
            return

        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.certfile, self.keyfile)

            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.host, self.port))
            self.sock.listen(10)
            self.sock.settimeout(2.0)  # 每秒醒来检查 running 标志
            self.running = True

            print(f"[*] 服务端监听 {self.host}:{self.port}")

            while self.running:
                try:
                    client_sock, addr = self.sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                print(f"[*] 新连接: {addr}，进行 TLS 握手...")

                try:
                    ssl_sock = ctx.wrap_socket(client_sock, server_side=True)
                except ssl.SSLError as e:
                    print(f"[-] TLS 握手失败 {addr}: {e}")
                    client_sock.close()
                    continue

                # 密码认证
                try:
                    data = ssl_sock.recv(1024).decode().strip()
                    if data == f"AUTH {self.password}":
                        ssl_sock.send(b"AUTH_OK\n")
                        print(f"[+] 认证通过: {addr}")
                        # 派发给 GUI 线程
                        self.new_client.emit(ssl_sock, addr)
                    else:
                        ssl_sock.send(b"AUTH_FAIL\n")
                        print(f"[-] 认证失败: {addr}")
                        ssl_sock.close()
                except Exception as e:
                    print(f"[-] 认证过程出错 {addr}: {e}")
                    ssl_sock.close()

        except Exception as e:
            self.server_error.emit(str(e))
        finally:
            self.running = False
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
            print("[*] 服务端监听已停止")

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


# ============================================================
# 主窗口
# ============================================================
class ScreenMonitorGUI(QMainWindow):
    """多客户端远程监控 GUI"""

    def __init__(self, host='0.0.0.0', port=8888,
                 certfile='server.crt', keyfile='server.key',
                 password='monitor123'):
        super().__init__()
        self._host = host
        self._port = port
        self._certfile = certfile
        self._keyfile = keyfile
        self._password = password

        # 状态
        self.sessions = {}          # ip -> ClientSession
        self.latest_frames = {}     # ip -> bytes (原始 JPEG)
        self.current_ip = None      # 当前选中的 IP
        self._mutex = QMutex()
        self._is_fullscreen = False

        self._init_ui()
        self._start_server()

    # ---------- UI 初始化 ----------

    def _init_ui(self):
        self.setWindowTitle("远程监控服务端")
        self.resize(1200, 800)

        # 中央部件
        central = QWidget()
        self.setCentralWidget(central)

        # 主布局：水平分割
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setHandleWidth(3)

        # ---- 左侧面板 ----
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(6, 6, 6, 6)

        ip_label = QLabel("📡 已连接客户端")
        ip_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 4px;")
        left_layout.addWidget(ip_label)

        self._ip_list = QListWidget()
        self._ip_list.setStyleSheet("""
            QListWidget {
                font-size: 13px;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px 6px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """)
        self._ip_list.itemClicked.connect(self._on_ip_clicked)
        left_layout.addWidget(self._ip_list)

        self._btn_disconnect = QPushButton("✕ 断开选中")
        self._btn_disconnect.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f; color: white;
                border: none; border-radius: 4px;
                padding: 8px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: #b71c1c; }
            QPushButton:disabled { background-color: #ccc; }
        """)
        self._btn_disconnect.clicked.connect(self._on_disconnect_clicked)
        self._btn_disconnect.setEnabled(False)
        left_layout.addWidget(self._btn_disconnect)

        left_panel.setMinimumWidth(180)
        left_panel.setMaximumWidth(350)

        # ---- 右侧面板 ----
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 6, 6, 6)

        # 工具栏
        toolbar = QHBoxLayout()
        self._lbl_view_title = QLabel("等待选择客户端...")
        self._lbl_view_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        toolbar.addWidget(self._lbl_view_title)
        toolbar.addStretch()

        self._btn_fullscreen = QPushButton("⛶ 全屏")
        self._btn_fullscreen.setStyleSheet("""
            QPushButton {
                background-color: #1976d2; color: white;
                border: none; border-radius: 4px;
                padding: 6px 14px; font-size: 12px;
            }
            QPushButton:hover { background-color: #1565c0; }
        """)
        self._btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        toolbar.addWidget(self._btn_fullscreen)

        self._btn_close_view = QPushButton("✕ 关闭画面")
        self._btn_close_view.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f; color: white;
                border: none; border-radius: 4px;
                padding: 6px 14px; font-size: 12px;
            }
            QPushButton:hover { background-color: #b71c1c; }
        """)
        self._btn_close_view.clicked.connect(self._on_close_view)
        toolbar.addWidget(self._btn_close_view)

        right_layout.addLayout(toolbar)

        # 画面显示区域
        self._screen_label = QLabel()
        self._screen_label.setAlignment(Qt.AlignCenter)
        self._screen_label.setStyleSheet("""
            QLabel {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 4px;
                color: #888;
                font-size: 16px;
            }
        """)
        self._screen_label.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Ignored
        )
        self._screen_label.setText("等待选择客户端...")
        right_layout.addWidget(self._screen_label)

        # 添加到分割器
        self._splitter.addWidget(left_panel)
        self._splitter.addWidget(right_panel)
        self._splitter.setStretchFactor(0, 0)   # 左侧不拉伸
        self._splitter.setStretchFactor(1, 1)   # 右侧拉伸
        self._splitter.setSizes([220, 980])

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._splitter)

        # 状态栏
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._lbl_status = QLabel("服务端未启动")
        self._status_bar.addWidget(self._lbl_status)

    # ---------- 服务端控制 ----------

    def _start_server(self):
        self._listener = ServerListener(
            self._host, self._port,
            self._certfile, self._keyfile,
            self._password
        )
        self._listener.new_client.connect(self._on_new_client)
        self._listener.server_error.connect(self._on_server_error)
        self._listener.start()
        self._update_status()

    def stop_server(self):
        # 停止所有客户端
        for ip in list(self.sessions.keys()):
            self._disconnect_client(ip)
        # 停止监听
        if hasattr(self, '_listener'):
            self._listener.stop()
            self._listener.wait(3000)

    # ---------- 信号处理 ----------

    def _on_new_client(self, ssl_sock, addr):
        ip = addr[0]

        # 如果该 IP 已有会话，先关闭旧的
        if ip in self.sessions:
            self._disconnect_client(ip)

        # 创建新会话
        session = ClientSession(ssl_sock, addr)
        session.frame_ready.connect(self._on_frame_ready)
        session.disconnected.connect(self._on_client_disconnected)
        session.start()
        self.sessions[ip] = session
        self.latest_frames[ip] = None

        # 添加到 IP 列表
        item = QListWidgetItem(f"  {ip}")
        item.setData(Qt.UserRole, ip)
        item.setToolTip(f"端口: {addr[1]}\n连接时间: {time.strftime('%H:%M:%S')}")
        self._ip_list.addItem(item)

        print(f"[+] 客户端已接入: {ip}")
        self._update_status()

    def _on_frame_ready(self, ip, jpeg_data):
        # 存储最新帧
        with QMutexLocker(self._mutex):
            self.latest_frames[ip] = jpeg_data

        # 仅当该 IP 是当前选中时，更新画面
        if ip == self.current_ip and jpeg_data:
            self._display_frame(jpeg_data)

    def _on_client_disconnected(self, ip):
        # 先移出会话，确保线程完全退出后再销毁对象
        session = self.sessions.pop(ip, None)
        if session:
            # 断开信号，防止重复触发
            try:
                session.frame_ready.disconnect(self._on_frame_ready)
                session.disconnected.disconnect(self._on_client_disconnected)
            except TypeError:
                pass
            if session.isRunning():
                session.wait(3000)

        self.latest_frames.pop(ip, None)

        # 移除列表项
        for i in range(self._ip_list.count()):
            item = self._ip_list.item(i)
            if item and item.data(Qt.UserRole) == ip:
                self._ip_list.takeItem(i)
                break

        # 如果断开的正是当前选中的，清空画面
        if ip == self.current_ip:
            self.current_ip = None
            self._clear_display()

        print(f"[-] 客户端已断开: {ip}")
        self._update_status()

    def _on_server_error(self, msg):
        QMessageBox.critical(self, "服务端错误", msg)

    # ---------- UI 交互 ----------

    def _on_ip_clicked(self, item):
        ip = item.data(Qt.UserRole)
        if ip == self.current_ip:
            return

        self.current_ip = ip
        self._lbl_view_title.setText(f"🖥 正在查看: {ip}")
        self._btn_disconnect.setEnabled(True)

        # 如果有帧数据，立即显示
        with QMutexLocker(self._mutex):
            data = self.latest_frames.get(ip)
        if data:
            self._display_frame(data)
        else:
            self._screen_label.setText(f"等待 {ip} 的画面数据...")

    def _on_disconnect_clicked(self):
        if self.current_ip:
            self._disconnect_client(self.current_ip)

    def _on_close_view(self):
        """关闭当前画面（断开客户端 + 清空显示）"""
        if self.current_ip:
            self._disconnect_client(self.current_ip)

    def _toggle_fullscreen(self):
        if self._is_fullscreen:
            self.showNormal()
            self._btn_fullscreen.setText("⛶ 全屏")
            self._is_fullscreen = False
        else:
            self.showFullScreen()
            self._btn_fullscreen.setText("⛶ 退出全屏")
            self._is_fullscreen = True

    # ---------- 内部操作 ----------

    def _disconnect_client(self, ip):
        if ip in self.sessions:
            session = self.sessions[ip]
            session.frame_ready.disconnect(self._on_frame_ready)
            session.disconnected.disconnect(self._on_client_disconnected)
            session.stop()
            session.wait(2000)
            del self.sessions[ip]

        if ip in self.latest_frames:
            del self.latest_frames[ip]

        # 移除列表项
        for i in range(self._ip_list.count()):
            item = self._ip_list.item(i)
            if item and item.data(Qt.UserRole) == ip:
                self._ip_list.takeItem(i)
                break

        if ip == self.current_ip:
            self.current_ip = None
            self._clear_display()

        print(f"[!] 已断开客户端: {ip}")
        self._update_status()

    def _display_frame(self, jpeg_data):
        """解码 JPEG 并显示在 QLabel 上"""
        try:
            nparr = np.frombuffer(jpeg_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return

            # OpenCV BGR → RGB
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, w * ch, QImage.Format_RGB888)

            # 缩放适配 QLabel，保持比例
            label_size = self._screen_label.size()
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._screen_label.setPixmap(scaled)
        except Exception as e:
            print(f"[-] 画面解码/显示失败: {e}")

    def _clear_display(self):
        self._screen_label.clear()
        self._screen_label.setText("无画面")
        self._lbl_view_title.setText("等待选择客户端...")
        self._btn_disconnect.setEnabled(False)

    def _update_status(self):
        count = len(self.sessions)
        self._lbl_status.setText(
            f"  已连接: {count} 个客户端  |  端口: {self._port}  |  "
            f"选中: {self.current_ip or '无'}"
        )

    # ---------- 窗口事件 ----------

    def resizeEvent(self, event):
        """窗口缩放时重绘当前画面"""
        super().resizeEvent(event)
        if self.current_ip:
            with QMutexLocker(self._mutex):
                data = self.latest_frames.get(self.current_ip)
            if data:
                self._display_frame(data)

    def closeEvent(self, event):
        self.stop_server()
        event.accept()


# ============================================================
# 入口
# ============================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description='远程监控服务端 GUI')
    parser.add_argument('--port', type=int, default=8888, help='监听端口')
    parser.add_argument('--cert', default='server.crt', help='证书路径')
    parser.add_argument('--key', default='server.key', help='私钥路径')
    parser.add_argument('--password', default=None,
                        help='认证密码 (默认: 环境变量 MONITOR_PASSWORD)')
    args = parser.parse_args()

    password = args.password or os.environ.get('MONITOR_PASSWORD') or 'monitor123'

    # 切换工作目录到项目根目录，确保证书路径正确
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # 全局暗色风格
    app.setStyleSheet("""
        QMainWindow, QWidget {
            background-color: #2b2b2b;
            color: #e0e0e0;
        }
        QSplitter::handle {
            background-color: #444;
        }
        QStatusBar {
            background-color: #1e1e1e;
            color: #aaa;
            border-top: 1px solid #444;
        }
        QLabel {
            background-color: transparent;
        }
    """)

    window = ScreenMonitorGUI(
        port=args.port,
        certfile=args.cert,
        keyfile=args.key,
        password=password
    )
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
