"""M1 验收脚本：扫描 test_images/ 下按前缀命名的样本，逐张调用 /api/analyze 并核对分级。

用法（在项目根目录 homework1/ 下）：
  1. 启动服务:  uvicorn backend.main:app --port 8000
  2. 运行测试:  python tools/test_m1.py [服务地址，默认 http://127.0.0.1:8000]

命名约定（文件名前缀即期望等级，每类可放多张）：
  high_1.jpg  medium_1.jpg  low_1.jpg  none_1.jpg  not_corridor_1.jpg
仅用标准库，无额外依赖。
"""

import base64
import json
import mimetypes
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES_DIR = ROOT / "test_images"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

PREFIX_TO_LEVEL = {
    "high": "high",
    "medium": "medium",
    "mid": "medium",
    "low": "low",
    "none": "none",
    "clean": "none",
    "not_corridor": "not_corridor",
}


def call_analyze(base_url: str, image_path: Path):
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    payload = json.dumps({"image": f"data:{mime};base64,{b64}"}).encode()
    req = urllib.request.Request(
        f"{base_url}/api/analyze",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:
            body = {}
        detail = body.get("detail", body) if isinstance(body, dict) else body
        return exc.code, detail


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

    cases = []
    if IMAGES_DIR.is_dir():
        for path in sorted(IMAGES_DIR.iterdir()):
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            expect = PREFIX_TO_LEVEL.get(path.stem.split("_")[0].lower())
            if expect:
                cases.append((path, expect))
    if not cases:
        print(f"未在 {IMAGES_DIR} 找到测试样本。")
        print("请按 high_1.jpg / medium_1.jpg / low_1.jpg / none_1.jpg / not_corridor_1.jpg 命名放入图片（见 test_images/README.md）。")
        return 1

    passed = 0
    for path, expect in cases:
        status, body = call_analyze(base_url, path)
        if status != 200 or not isinstance(body, dict):
            print(f"[FAIL] {path.name}: HTTP {status} {body}")
            continue
        got = body.get("risk_level")
        ok = got == expect
        passed += ok
        print(
            f"[{'PASS' if ok else 'FAIL'}] {path.name}: 期望 {expect}, "
            f"实际 {got}({body.get('risk_label')}), 置信度 {body.get('confidence')}, "
            f"耗时 {body.get('elapsed_ms')}ms"
        )
        print(f"       元素: {body.get('elements')}")
        print(f"       依据: {body.get('reason')}")

    print(f"\n合计 {passed}/{len(cases)} 通过")
    return 0 if passed == len(cases) else 2


if __name__ == "__main__":
    sys.exit(main())
