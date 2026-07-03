"""
远程屏幕推送客户端（加密版 · 自动重连）

与服务端 (gui/server.py) 配合使用，持续截屏并通过 TLS 加密通道推送。
"""
import ssl
import socket
import argparse
import os
import time


# ---------- 截图引擎 ----------
def _get_screenshot_engine():
    """按优先级返回截图函数: mss > Pillow > 失败"""
    try:
        import mss
        _sct = mss.MSS()
        def capture():
            try:
                return _sct.grab(_sct.monitors[0])
            except Exception:
                return None
        print("[+] 截图引擎: mss (高速)")
        return capture
    except Exception:
        pass

    try:
        from PIL import ImageGrab
        print("[+] 截图引擎: Pillow ImageGrab")
        return ImageGrab.grab
    except ImportError:
        pass

    print("[-] 未安装截图库，请执行: pip install mss")
    return None


def encode_jpeg(image, quality=70):
    """将 PIL/mss Image 编码为 JPEG 字节"""
    try:
        from PIL import Image
        import io
        if hasattr(image, 'rgb') and hasattr(image, 'size'):
            img = Image.frombytes('RGB', image.size, image.rgb)
        else:
            img = image
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"[-] JPEG 编码失败: {e}")
        return None


def send_frame(sock, jpeg_bytes):
    """发送一帧：16字节长度头 + JPEG数据"""
    size = len(jpeg_bytes)
    header = f"{size:<16}".encode()
    sock.sendall(header + jpeg_bytes)


def connect_and_push(host, port, password, capture, quality, frame_interval):
    """连接服务端并推送画面，断开时返回"""
    sock = None
    try:
        raw_sock = socket.socket()
        raw_sock.settimeout(15)
        raw_sock.connect((host, port))

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(raw_sock, server_hostname=host)

        # 认证
        sock.send(f"AUTH {password}\n".encode())
        auth_resp = sock.recv(1024).decode().strip()
        if auth_resp != "AUTH_OK":
            print(f"[-] 认证失败: {auth_resp}")
            return

        print("[+] 认证通过，开始推送画面...")

        # 推送循环
        while True:
            loop_start = time.perf_counter()

            raw = capture()
            if raw is None:
                time.sleep(frame_interval)
                continue

            jpeg = encode_jpeg(raw, quality=quality)
            if jpeg is None:
                time.sleep(frame_interval)
                continue

            send_frame(sock, jpeg)

            elapsed = time.perf_counter() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except (ssl.SSLError, ConnectionError, OSError, socket.timeout) as e:
        print(f"[-] 连接异常: {e}")
    except KeyboardInterrupt:
        print("\n[*] 用户中断")
        raise
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


# ---------- 主程序 ----------
def main():
    parser = argparse.ArgumentParser(description='远程屏幕推送客户端 (加密版 · 自动重连)')
    parser.add_argument('--host', default='127.0.0.1', help='服务端 IP (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=8888, help='服务端端口 (默认: 8888)')
    parser.add_argument('--password', default=None,
                        help='认证密码 (默认: 环境变量 MONITOR_PASSWORD)')
    parser.add_argument('--fps', type=float, default=5.0,
                        help='推送帧率 (默认: 5，公网建议 1-3)')
    parser.add_argument('--quality', type=int, default=70,
                        help='JPEG 压缩质量 1-100 (默认: 70，公网建议 40-60)')
    parser.add_argument('--reconnect-delay', type=float, default=1.0,
                        help='断线重连间隔秒数 (默认: 1)')
    args = parser.parse_args()

    password = args.password or os.environ.get('MONITOR_PASSWORD') or 'monitor123'
    frame_interval = 1.0 / args.fps

    capture = _get_screenshot_engine()
    if capture is None:
        return

    print(f"[*] 目标服务器: {args.host}:{args.port}")
    print(f"[*] 帧率: {args.fps}fps  |  质量: {args.quality}  |  自动重连: 每 {args.reconnect_delay} 秒")
    print("-" * 50)

    while True:
        print(f"[*] 正在连接 {args.host}:{args.port}...")
        try:
            connect_and_push(
                args.host, args.port, password,
                capture, args.quality, frame_interval
            )
        except KeyboardInterrupt:
            print("[*] 客户端已退出")
            break

        print(f"[*] {args.reconnect_delay} 秒后重连...")
        time.sleep(args.reconnect_delay)


if __name__ == '__main__':
    main()
