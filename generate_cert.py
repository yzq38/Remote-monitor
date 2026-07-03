"""
生成自签名 TLS 证书，用于远程监控服务的加密通信。

用法:
    python generate_cert.py

将生成 server.crt（证书）和 server.key（私钥）两个文件。
"""
import os
import sys
import datetime

# 检查 cryptography 是否可用
try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("[-] 需要安装 cryptography 库: pip install cryptography")
    sys.exit(1)


def generate_self_signed_cert(cert_file='server.crt', key_file='server.key'):
    """生成自签名证书和私钥"""

    # 生成 RSA 私钥（2048 位）
    print("[*] 正在生成 RSA 私钥...")
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # 构建证书信息
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Remote Monitor"),
    ])

    # 构建并签名证书
    print("[*] 正在生成自签名证书（有效期 10 年）...")

    now = datetime.datetime.now(datetime.UTC)
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
    )

    # 添加 SAN（Subject Alternative Name），使证书可用于多种连接方式
    cert_builder = cert_builder.add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
        ]),
        critical=False,
    )

    # 签名
    cert = cert_builder.sign(key, hashes.SHA256())

    # 写入证书文件
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[+] 证书已生成: {os.path.abspath(cert_file)}")

    # 写入私钥文件
    with open(key_file, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    print(f"[+] 私钥已生成: {os.path.abspath(key_file)}")

    print()
    print("[*] 使用方式:")
    print(f"  服务端: python server.py --cert {cert_file} --key {key_file}")
    print(f"  客户端: python client.py --host <服务器IP>")
    print()
    print("[!] 警告: 这是自签名证书，仅供测试使用。")
    print("    生产环境建议使用 Let's Encrypt 等正规 CA 签发的证书。")


if __name__ == '__main__':
    generate_self_signed_cert()
