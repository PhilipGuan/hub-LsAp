from pathlib import Path

import yaml


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        with path.open(encoding="utf-8") as file:
            config = yaml.safe_load(file)
    except FileNotFoundError:
        raise RuntimeError("Copy config.example.yaml to config.yaml and fill in the settings.") from None
    except yaml.YAMLError:
        raise ValueError("config.yaml is not valid YAML.") from None
    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain a mapping.")
    if "data_dir" in config:
        config["data_dir"] = (path.resolve().parent / config["data_dir"]).resolve()
    return config
