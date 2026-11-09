"""楼道消防隐患识别系统 - 后端服务

FastAPI + DeepSeek V4.1 Flash (deepseek-flash) 多模态模型
- POST /api/recognize: 接收图片，返回风险等级与判断理由
- 托管前端静态页面，支持 HTTPS（自签名证书）供手机局域网访问摄像头
"""
import base64
import ipaddress
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL_NAME = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"

PROMPT = """你是一个专业消防专家，擅长识别各种风险。请帮我识别消防安全隐患以及隐患的危险程度。根据楼道中是否存在大量堆积物，堆积物是否可燃以及是否有起火风险将隐患分为无隐患、低风险、中等风险、高风险。

- 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
- 中风险：楼道中存在大量堆积物严重影响通行或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
- 低风险：存在楼道堆物现象但不严重。
- 无风险：楼道干净，无堆放物品。
- 非楼道：一些与楼道无关的图片。

请严格按以下JSON格式输出，不要输出任何其他内容：
{"level": "高风险|中风险|低风险|无风险|非楼道 中的唯一一个", "reason": "一句话判断理由"}"""

VALID_LEVELS = ["高风险", "中风险", "低风险", "无风险", "非楼道"]
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB

app = FastAPI(title="楼道消防隐患识别")

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL) if DEEPSEEK_API_KEY else None


def parse_result(text: str) -> dict:
    """解析模型输出为 {level, reason}，带容错"""
    text = text.strip()
    # 去掉可能的 markdown 代码块包裹
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        data = json.loads(text)
        level = str(data.get("level", "")).strip()
        reason = str(data.get("reason", "")).strip()
    except json.JSONDecodeError:
        level = ""
        for lv in VALID_LEVELS:
            if lv in text:
                level = lv
                break
        reason = text[:120]
    if level not in VALID_LEVELS:
        raise ValueError(f"模型未给出有效风险等级: {text[:120]}")
    return {"level": level, "reason": reason or "未提供理由"}


@app.post("/api/recognize")
async def recognize(file: UploadFile = File(...)):
    if not client:
        raise HTTPException(500, "未配置 DEEPSEEK_API_KEY，请在 .env 中填写后重启服务")
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"仅支持 JPG/PNG/WebP 图片，收到: {file.content_type}")

    data = await file.read()
    if not data:
        raise HTTPException(400, "图片内容为空")
    if len(data) > MAX_SIZE:
        raise HTTPException(400, "图片不能超过 10MB")

    b64 = base64.b64encode(data).decode()
    try:
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{file.content_type};base64,{b64}"},
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
            max_tokens=500,
            temperature=0.1,
            # 禁用思考模式，避免输出预算被思维链耗尽导致正文为空
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as e:
        raise HTTPException(502, f"模型调用失败: {e}")

    raw = (resp.choices[0].message.content or "").strip()
    try:
        result = parse_result(raw)
    except ValueError as e:
        raise HTTPException(502, f"识别结果解析失败: {e}")

    return {"level": result["level"], "reason": result["reason"], "raw": raw}


# ---- HTTPS 自签名证书 ----

def get_lan_ip() -> str:
    """获取本机局域网 IP（不真正发包）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert() -> tuple[Path, Path]:
    """证书不存在或局域网 IP 变化时重新生成自签名证书"""
    cert_file = BASE_DIR / "cert.pem"
    key_file = BASE_DIR / "key.pem"
    ip_file = BASE_DIR / ".cert_ip"
    lan_ip = get_lan_ip()

    if cert_file.exists() and key_file.exists() and ip_file.exists():
        if ip_file.read_text().strip() == lan_ip:
            return cert_file, key_file

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fire-hazard-app")])
    san = x509.SubjectAlternativeName([
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address(lan_ip)),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    ip_file.write_text(lan_ip)
    print(f"[cert] 已生成自签名证书 (SAN: localhost, 127.0.0.1, {lan_ip})")
    return cert_file, key_file


# 静态前端（挂在 /api 路由之后）
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8443"))
    cert_file, key_file = ensure_cert()
    lan_ip = get_lan_ip()
    print("=" * 56)
    print("  楼道消防隐患识别系统已启动")
    print(f"  电脑访问:  https://localhost:{port}")
    print(f"  手机访问:  https://{lan_ip}:{port}")
    print("  (手机需与电脑同一 WiFi；首次访问需信任自签名证书)")
    print("=" * 56)
    uvicorn.run(app, host="0.0.0.0", port=port, ssl_certfile=str(cert_file), ssl_keyfile=str(key_file))
