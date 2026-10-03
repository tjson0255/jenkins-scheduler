""".env の読み書き。

読み込みは設定（app/config.py）から、書き込みはインストーラ・CLI から使う。書き込むときはコメントや他の行をそのまま残す。
"""

from __future__ import annotations

import re
from pathlib import Path

_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def read_values(path: Path) -> dict[str, str]:
    """.env を {KEY: VALUE} にする（UTF-8）。

    書き方: KEY=VALUE（前後の空白は無視）、# で始まる行はコメント、export KEY=VALUE も可。
    値を '…' か "…" で囲むと、中の # や前後の空白もそのまま値になる（"…" の中では \\n が改行）。
    囲まない値は、空白の後ろの # 以降をコメントとして捨てる。
    """
    values: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _KEY.match(line)
        if not m:
            continue
        value = line[m.end():].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n").replace('\\"', '"')
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[m.group(1)] = value
    return values


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
