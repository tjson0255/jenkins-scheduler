""".env の読み書き（インストーラ・CLI から設定を書き込むため）。コメントや他の行はそのまま残す。"""

from __future__ import annotations

import re
from pathlib import Path

_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def set_values(path: Path, values: dict[str, str | None]) -> None:
    """KEY=VALUE を設定する。値が None のキーは行ごと消す。無いキーは末尾に足す。"""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        m = _KEY.match(line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            value = remaining.pop(key)
            if value is not None:
                out.append(f"{key}={value}")
            continue
        out.append(line)
    added = [f"{k}={v}" for k, v in remaining.items() if v is not None]
    if added:
        if out and out[-1].strip():
            out.append("")
        out.extend(added)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def init_from_example(env_path: Path, example_path: Path) -> bool:
    """.env が無ければ .env.example から作る。作ったら True。"""
    env_path, example_path = Path(env_path), Path(example_path)
    if env_path.exists():
        return False
    env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return True
