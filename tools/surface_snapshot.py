# SPDX-License-Identifier: Apache-2.0
"""관측 가능한 표면의 지문 — 정리 작업이 행동을 바꾸지 않았음을 증명한다.

9단계(코드 품질)는 **행동을 바꾸지 않는 변경**만 한다. 그것을 "테스트가
초록이다"로만 확인하면 부족하다 — 테스트가 보지 않는 표면(임포트 경로,
MCP 도구 스키마, 공개 시그니처)이 조용히 바뀔 수 있고, 파일을 옮기다
임포트를 놓치면 **테스트가 수집되지 않아 그냥 줄어든다**(초록인 채로).

그래서 표면을 파일로 찍어 두고 전후를 대조한다. 사용법:

    python tools/surface_snapshot.py > /tmp/before.txt   # 정리 전
    ... 정리 ...
    python tools/surface_snapshot.py > /tmp/after.txt
    diff /tmp/before.txt /tmp/after.txt                  # 반드시 빈 diff
"""

from __future__ import annotations

import dataclasses
import re
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _emit(line: str) -> None:
    print(line)


def dump_modules() -> None:
    """공개 임포트 경로 — 파일을 옮겨도 여기 이름이 사라지면 안 된다."""
    import anchor

    names = sorted(
        m.name for m in pkgutil.walk_packages(anchor.__path__, "anchor.")
    )
    for name in names:
        _emit(f"module {name}")


def dump_public_api() -> None:
    """공개 클래스·함수의 시그니처와 dataclass 필드."""
    import anchor

    targets = sorted(
        m.name for m in pkgutil.walk_packages(anchor.__path__, "anchor.")
    ) + ["anchor"]
    for mod_name in sorted(set(targets)):
        try:
            mod = importlib.import_module(mod_name)
        except Exception as error:  # 임포트가 깨지면 그 자체가 회귀다
            _emit(f"IMPORT-FAILED {mod_name}: {type(error).__name__}")
            continue
        for attr in sorted(dir(mod)):
            if attr.startswith("_"):
                continue
            obj = getattr(mod, attr)
            if getattr(obj, "__module__", None) != mod_name:
                continue  # 재수출은 원 정의 모듈에서 한 번만 찍는다
            if dataclasses.is_dataclass(obj):
                fields = ", ".join(
                    f"{f.name}:{_type_name(f.type)}" for f in dataclasses.fields(obj)
                )
                _emit(f"dataclass {mod_name}.{attr}({fields})")
            elif inspect.isclass(obj):
                _emit(f"class {mod_name}.{attr}({_bases(obj)})")
                for meth in sorted(dir(obj)):
                    if meth.startswith("_") and meth != "__init__":
                        continue
                    fn = getattr(obj, meth, None)
                    if inspect.isfunction(fn):
                        _emit(f"  def {meth}{_signature(fn)}")
            elif inspect.isfunction(obj):
                _emit(f"def {mod_name}.{attr}{_signature(obj)}")
            elif isinstance(obj, (str, int, float, bool, frozenset, tuple)):
                _emit(_stable(f"const {mod_name}.{attr} = {obj!r}"))


def dump_namespaces() -> None:
    """모듈마다 **살고 있는 이름 전부** — 사적 이름과 재수출까지.

    `dump_public_api()`는 이것을 구조적으로 못 본다: `_`로 시작하는 속성을
    건너뛰고, `__module__`이 다른 재수출도 건너뛴다. 그래서 9-가(호출 기제
    분리)가 `anchor.service`에서 이름 8개를 없앴는데 지문은 **"삭제 0"을
    찍었다** — 안 봐서 0이었다:

        functools · sys · threading · Iterator · contextmanager
        StorageError · approx · _UrlLockEntry

    그때는 감사자가 손으로 `vars()`를 떠서 메웠고 전수 grep으로 아무도 그
    이름들을 참조하지 않음을 확인해 무해로 끝났다. 그러나 **다음 정리가
    사적 이름을 옮길 때 이 도구는 또 침묵한다.** 증명 도구가 못 보는 축이
    있다는 것을 알고도 두면, 그 축에서 벌어지는 일은 영영 증거가 없다.

    이름 목록만 찍는다(시그니처는 위에서 이미 본다). 임포트 한 줄만 옮겨도
    diff가 뜨는데, 그것이 정확히 이 단계에서 원하는 것이다 — "아무것도 안
    바뀌어야 한다"가 계약이므로 여기서는 **잡음이 곧 신호다**.
    """
    import anchor

    targets = sorted(
        {m.name for m in pkgutil.walk_packages(anchor.__path__, "anchor.")} | {"anchor"}
    )
    for mod_name in targets:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as error:
            _emit(f"NAMESPACE-IMPORT-FAILED {mod_name}: {type(error).__name__}")
            continue
        names = sorted(n for n in vars(mod) if not n.startswith("__"))
        _emit(f"namespace {mod_name} = {', '.join(names)}")


def _type_name(t: object) -> str:
    return getattr(t, "__name__", str(t))


def _bases(cls: type) -> str:
    return ", ".join(b.__name__ for b in cls.__bases__)


# `repr`에 실려 오는 실행마다 달라지는 것들. 지문이 이것을 담으면 **아무것도
# 바꾸지 않아도 매번 diff가 뜨고**(실측 18줄 — typer의 OptionInfo/ArgumentInfo),
# 규약 R3의 "삭제·변경 0줄" 검사가 무력해진다. 더 나쁜 것은 그 잡음을 손수
# "무해"로 판정하는 습관이 붙는 것이다 — 진짜 시그니처 변경이 같은 자리에
# 섞이면 놓친다. 증명 도구는 결정적이어야 한다.
_VOLATILE = (
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),          # 메모리 주소
    (re.compile(r"<([\w.]+) object at 0xADDR>"), r"<\1>"),  # 주소를 뗀 뒤 남는 껍질
)


def _stable(text: str) -> str:
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return text


def _signature(fn: object) -> str:
    try:
        return _stable(str(inspect.signature(fn)))
    except (ValueError, TypeError):
        return "(?)"


def dump_mcp_surface() -> None:
    """MCP 도구 이름과 입력 스키마 — 에이전트가 보는 계약."""
    try:
        from anchor.server import build_server
    except Exception as error:
        _emit(f"MCP-IMPORT-FAILED {type(error).__name__}")
        return
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        try:
            server, service = build_server(db_path=Path(tmp) / "s.db")
        except Exception as error:
            _emit(f"MCP-BUILD-FAILED {type(error).__name__}: {error}")
            return
        try:
            tools = getattr(server, "_tools", None) or getattr(server, "tools", None)
            if tools is None:
                _emit("MCP tools: (서버 내부 표현을 못 찾음 — 수동 확인 필요)")
            else:
                for name in sorted(tools):
                    _emit(f"mcp-tool {name}")
        finally:
            service.close()


def dump_schema() -> None:
    """SQLite 스키마 — 정리가 스키마를 건드리면 안 된다."""
    from anchor.store.repository import MIGRATION_FILES, SCHEMA_VERSION

    _emit(f"schema-version {SCHEMA_VERSION}")
    for version in sorted(MIGRATION_FILES):
        _emit(f"migration {version} {MIGRATION_FILES[version]}")


def main() -> int:
    _emit("### modules")
    dump_modules()
    _emit("### public api")
    dump_public_api()
    _emit("### namespaces")
    dump_namespaces()
    _emit("### mcp")
    dump_mcp_surface()
    _emit("### schema")
    dump_schema()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
