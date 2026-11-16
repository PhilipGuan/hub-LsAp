import json
import os
import subprocess
import zipfile

import config


def parse_pdf(pdf_path, out_dir):
    """用 mineru 解析 PDF，解压产物。

    返回 dict:
        texts:        所有 .md 文本列表（降级用）
        images:       图片相对路径列表（降级用）
        content_list: 结构化内容块列表（img_path 已转成相对 PARSE_DIR 路径）
        extract_dir:  解压目录
    """
    os.makedirs(out_dir, exist_ok=True)
    doc_name = os.path.splitext(os.path.basename(pdf_path))[0]
    zip_path = os.path.join(out_dir, doc_name + ".zip")

    cmd = [
        "mineru-kit", "parse", pdf_path,
        "--format", "zip",
        "-o", zip_path,
        "--tier", "advanced",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    extract_dir = os.path.join(out_dir, doc_name)
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)

    texts, images = [], []
    for root, _, files in os.walk(extract_dir):
        for f in files:
            fp = os.path.join(root, f)
            if f.endswith(".md"):
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    texts.append(fh.read())
            elif f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                images.append(fp)

    rel_images = [os.path.relpath(p, config.PARSE_DIR) for p in images]
    # 图片文件名 -> 相对 PARSE_DIR 路径，用于校正 content_list 里的 img_path
    img_map = {os.path.basename(p): rp for p, rp in zip(images, rel_images)}

    return {
        "texts": texts,
        "images": rel_images,
        "content_list": _load_content_list(extract_dir, img_map),
        "extract_dir": extract_dir,
    }


def _load_content_list(extract_dir, img_map):
    """读取 *_content_list.json，把 img_path 统一转成相对 PARSE_DIR 的路径"""
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.endswith("_content_list.json"):
                fp = os.path.join(root, f)
                with open(fp, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    data = data.get("content_list") or data.get("docs") or []
                out = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    cp = dict(item)
                    img = cp.get("img_path") or ""
                    if img:
                        cp["img_path"] = img_map.get(os.path.basename(img), img)
                    out.append(cp)
                return out
    return []