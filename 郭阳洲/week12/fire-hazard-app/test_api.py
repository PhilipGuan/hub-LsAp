"""端到端测试：上传指定图片到识别接口（用法: python test_api.py 图片路径）"""
import json
import ssl
import sys
import time
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

boundary = "----TraeFormBoundary7d1a2c"
image_path = sys.argv[1] if len(sys.argv) > 1 else "test_corridor.jpg"
with open(image_path, "rb") as f:
    img = f.read()

body = (
    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.jpg"\r\n'
    f"Content-Type: image/jpeg\r\n\r\n"
).encode() + img + f"\r\n--{boundary}--\r\n".encode()

t0 = time.time()
req = urllib.request.Request(
    "https://localhost:8443/api/recognize",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
try:
    r = urllib.request.urlopen(req, context=ctx, timeout=120)
    print(f"HTTP {r.status} | 耗时 {time.time() - t0:.1f}s")
    print(json.dumps(json.loads(r.read().decode()), ensure_ascii=False, indent=2))
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code} | 耗时 {time.time() - t0:.1f}s")
    print(e.read().decode())
