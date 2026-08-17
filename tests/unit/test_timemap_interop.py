# SPDX-License-Identifier: Apache-2.0
"""상호운용 (SPEC §12): 생성한 TimeMap을 독립 구현 파서로 검증한다.

파서는 본 테스트가 RFC 9264(link-format) 문법을 따로 구현한 것으로,
직렬화 코드와 어떤 것도 공유하지 않는다 — HTTP-date 안의 쉼표를 항목
구분자로 오인하면 안 되는 것까지 포함해, 외부 Memento 클라이언트가 읽는
방식 그대로 읽는다.
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime

from anchor.export.timemap import to_link_format
from tests.unit.test_exporters import DOC, V1, V2, V_ARCHIVE


def parse_link_format(body: str) -> list[dict]:
    """RFC 9264 link-format의 독립 파서 (따옴표 안 쉼표를 존중)."""
    entries: list[dict] = []
    current = ""
    in_quotes = False
    in_uri = False
    for char in body:
        if char == '"' and not in_uri:
            in_quotes = not in_quotes
        elif char == "<" and not in_quotes:
            in_uri = True
        elif char == ">" and not in_quotes:
            in_uri = False
        if char == "," and not in_quotes and not in_uri:
            entries.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        entries.append(current.strip())

    parsed = []
    for entry in entries:
        uri_part, *params = entry.split(";")
        assert uri_part.strip().startswith("<") and uri_part.strip().endswith(">"), entry
        record: dict = {"uri": uri_part.strip()[1:-1]}
        for param in params:
            key, _, value = param.strip().partition("=")
            record[key] = value.strip('"')
        parsed.append(record)
    return parsed


def test_timemap_is_parseable_by_independent_rfc_parser():
    body = to_link_format(DOC, [V1, V_ARCHIVE, V2])
    records = parse_link_format(body)

    originals = [r for r in records if r.get("rel") == "original"]
    selves = [r for r in records if r.get("rel") == "self"]
    mementos = [r for r in records if "memento" in r.get("rel", "")]

    assert len(originals) == 1 and originals[0]["uri"] == DOC.url
    assert len(selves) == 1 and selves[0]["type"] == "application/link-format"
    assert len(mementos) == 3

    # 모든 memento는 RFC 1123 HTTP-date를 가져야 한다 (Memento-Datetime).
    for memento in mementos:
        moment = parsedate_to_datetime(memento["datetime"])
        assert moment.tzinfo is not None
        assert memento["uri"]

    # first/last 한 번씩, 시간 오름차순.
    assert sum("first" in m["rel"] for m in mementos) == 1
    assert sum("last" in m["rel"] for m in mementos) == 1
    datetimes = [parsedate_to_datetime(m["datetime"]) for m in mementos]
    assert datetimes == sorted(datetimes)
