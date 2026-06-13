"""storage 目录约定。所有入库路径一律存「相对 backend 根」的相对路径,
只有真正读写文件时才用 abs_from_rel() 还原为绝对路径。"""

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
STORAGE_ROOT = BACKEND_ROOT / "storage"
REFERENCES_SUBDIR = "storage/references"
IMAGES_SUBDIR = "storage/images"


def ensure_dirs(base_dir: Path = BACKEND_ROOT) -> None:
    (base_dir / REFERENCES_SUBDIR).mkdir(parents=True, exist_ok=True)
    (base_dir / IMAGES_SUBDIR).mkdir(parents=True, exist_ok=True)


def abs_from_rel(rel: str, base_dir: Path = BACKEND_ROOT) -> Path:
    return base_dir / rel
