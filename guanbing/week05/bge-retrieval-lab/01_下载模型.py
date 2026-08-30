"""
Step 1: 下载 BAAI/bge-small-zh-v1.5 到本地 BAAI/bge-small-zh-v1.5 目录
策略（全部缓存重定向到项目内部，避免写入 ~ 目录）：
  1) huggingface_hub.snapshot_download（HF 端点或镜像）
  2) modelscope（环境变量 + monkey patch 双保险）
  3) 命令行调用 modelscope CLI
"""
import os
import sys
import shutil

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_MODEL_DIR = os.path.join(PROJECT_DIR, "BAAI", "bge-small-zh-v1.5")
CACHE_DIR = os.path.join(PROJECT_DIR, ".cache")
MODEL_NAME = "BAAI/bge-small-zh-v1.5"

os.environ["MODELSCOPE_CACHE"] = os.path.join(CACHE_DIR, "modelscope")
os.environ["HF_HOME"] = os.path.join(CACHE_DIR, "huggingface")
os.environ["TRANSFORMERS_CACHE"] = os.path.join(CACHE_DIR, "transformers")
os.environ["SENTENCE_TRANSFORMERS_HOME"] = os.path.join(CACHE_DIR, "sentence_transformers")
os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(CACHE_DIR, "hf_hub")
for k in ["MODELSCOPE_CACHE", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "SENTENCE_TRANSFORMERS_HOME"]:
    os.makedirs(os.environ[k], exist_ok=True)


def copy_tree(src: str, dst: str):
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        s = os.path.join(src, f)
        d = os.path.join(dst, f)
        if os.path.exists(d):
            continue
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)


def try_hf_snapshot() -> bool:
    print("   🅰️  方案 A: huggingface_hub.snapshot_download (含镜像端点)")
    try:
        from huggingface_hub import snapshot_download
    except Exception as e:
        print(f"   ⚠️  huggingface_hub 未安装: {e}")
        return False

    endpoints = [None]
    for name in ["HF_ENDPOINT", "HF_MIRROR"]:
        if os.environ.get(name):
            endpoints.insert(0, os.environ[name])
    endpoints.append("https://hf-mirror.com")

    for ep in endpoints:
        try:
            if ep:
                print(f"      尝试端点: {ep}")
                os.environ["HF_ENDPOINT"] = ep
            folder = snapshot_download(
                MODEL_NAME,
                cache_dir=os.environ["HUGGINGFACE_HUB_CACHE"],
                local_dir=LOCAL_MODEL_DIR,
                local_dir_use_symlinks=False,
                resume_download=True,
            )
            if folder and os.path.isdir(folder) and len(os.listdir(folder)) >= 5:
                if os.path.realpath(folder) != os.path.realpath(LOCAL_MODEL_DIR):
                    copy_tree(folder, LOCAL_MODEL_DIR)
                return True
        except Exception as e:
            print(f"      ⚠️  失败: {e}")
    return False


def try_modelscope_snapshot() -> bool:
    print("   🅱️  方案 B: modelscope.snapshot_download")
    import pathlib
    # Monkey patch: 让 modelscope 不去碰 ~/.modelscope
    import modelscope_hub.config as mscfg  # type: ignore
    mscfg.DEFAULT_CACHE_DIR = pathlib.Path(os.environ["MODELSCOPE_CACHE"])
    try:
        from modelscope import snapshot_download
    except Exception as e:
        print(f"   ⚠️  modelscope 未安装: {e}")
        return False

    try:
        folder = snapshot_download(
            MODEL_NAME,
            cache_dir=os.environ["MODELSCOPE_CACHE"],
        )
    except TypeError:
        try:
            folder = snapshot_download(
                MODEL_NAME,
                cache_dir=os.environ["MODELSCOPE_CACHE"],
                local_dir=LOCAL_MODEL_DIR,
            )
            if os.path.isdir(LOCAL_MODEL_DIR) and len(os.listdir(LOCAL_MODEL_DIR)) >= 5:
                return True
        except Exception as e:
            print(f"   ⚠️  local_dir 也失败: {e}")
            return False
    except Exception as e:
        print(f"   ⚠️  缓存模式失败: {e}")
        return False

    if folder and os.path.isdir(folder):
        copy_tree(folder, LOCAL_MODEL_DIR)
        return True
    return False


def try_modelscope_cli() -> bool:
    print("   🅲  方案 C: 调用 modelscope CLI 命令")
    import subprocess
    cmd = [
        sys.executable, "-m", "modelscope", "download",
        "--model", MODEL_NAME,
        "--local_dir", LOCAL_MODEL_DIR,
    ]
    env = os.environ.copy()
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and os.path.isdir(LOCAL_MODEL_DIR) and len(os.listdir(LOCAL_MODEL_DIR)) >= 5:
            return True
        print(f"   ⚠️  CLI 非零返回: rc={r.returncode}")
        if r.stderr:
            print("      STDERR:", r.stderr[:300])
        return False
    except Exception as e:
        print(f"   ⚠️  CLI 失败: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print(f"[Step 1] 下载模型 {MODEL_NAME}")
    print("=" * 60)
    print(f"本地保存路径: {LOCAL_MODEL_DIR}")
    print(f"缓存目录: {CACHE_DIR}\n")

    if os.path.isdir(LOCAL_MODEL_DIR) and len(os.listdir(LOCAL_MODEL_DIR)) >= 5:
        print(f"✅ 模型目录已存在，跳过下载")
        print(f"   重新下载: rm -rf {LOCAL_MODEL_DIR} {CACHE_DIR}")
        sys.exit(0)

    ok = False
    for fn in [try_hf_snapshot, try_modelscope_snapshot, try_modelscope_cli]:
        ok = fn()
        if ok:
            break
        print()

    if not ok:
        print("\n❌ 所有下载方案失败，请在独立终端（非沙盒）执行:")
        print("   cd " + PROJECT_DIR)
        print("   export MODELSCOPE_CACHE=" + os.environ["MODELSCOPE_CACHE"])
        print("   modelscope download --model " + MODEL_NAME + " --local_dir " + LOCAL_MODEL_DIR)
        sys.exit(1)

    files = sorted(os.listdir(LOCAL_MODEL_DIR))
    print(f"\n✅ 模型下载完成!")
    print(f"   路径: {LOCAL_MODEL_DIR}")
    print(f"   文件数: {len(files)} -> {files}")
