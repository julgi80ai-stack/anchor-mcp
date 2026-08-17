#!/usr/bin/env python3
"""제3자 구성요소 라이선스 감사.

추측이나 기억이 아니라 배포자가 선언한 메타데이터를 직접 읽는다.
라이선스는 실제로 변경된다 — trafilatura는 v1.8.0에서 GPLv3+에서
Apache-2.0으로 바뀌었고, 그 전 버전을 끌어오면 Anchor의 Apache-2.0
배포와 충돌한다. 그래서 이 검사는 사람의 주의력이 아니라 CI에 맡긴다.

사용:
    python3 tools/audit_licenses.py                    # 조회만
    python3 tools/audit_licenses.py --fail-on-copyleft # CI 게이트
    python3 tools/audit_licenses.py --check-floors     # 버전 하한 검사
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

# Apache-2.0 배포와 호환되지 않는 라이선스 계열.
# LGPL은 정적 링크 시 문제가 되므로 함께 차단한다.
COPYLEFT_MARKERS: tuple[str, ...] = ("GPL", "AGPL", "LGPL", "SSPL", "BUSL")

# 배포물에 포함되는 런타임 의존성. 여기에 없는 패키지가 설치 목록에
# 나타나면 THIRD-PARTY.md 갱신이 누락된 것이다.
RUNTIME_PACKAGES: tuple[str, ...] = (
    "trafilatura",
    "readability-lxml",
    "lxml",
    "markdownify",
    "regex",
    "httpx",
    "protego",
    "charset-normalizer",
    "blake3",
    "zstandard",
    "pypdf",
    "pydantic",  # mcp 트랜지티브 — 직접 사용 없음
    "typer",
    "mcp",
    "beautifulsoup4",  # markdownify 트랜지티브
    "soupsieve",  # beautifulsoup4 트랜지티브
)

# 라이선스 변경 이력이 있어 버전 하한이 필요한 패키지.
# (패키지, 최소 버전, 사유)
VERSION_FLOORS: tuple[tuple[str, str, str], ...] = (
    ("trafilatura", "1.8.0", "v1.8.0 미만은 GPLv3+ (Apache-2.0 배포와 충돌)"),
)

PYPI_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class LicenseRecord:
    """한 패키지의 라이선스 조회 결과."""

    package: str
    version: str
    license: str
    source: str  # 값을 읽어낸 메타데이터 필드명

    @property
    def is_copyleft(self) -> bool:
        return any(marker in self.license.upper() for marker in COPYLEFT_MARKERS)


def fetch_license(package: str) -> LicenseRecord:
    """PyPI JSON API에서 라이선스를 조회한다.

    최신 패키지들은 `license` 필드를 비우고 PEP 639의
    `license_expression`이나 Trove classifier로만 표기하는 경우가
    많아졌다. 한 곳만 보면 UNKNOWN이 대량으로 나오므로 세 곳을
    우선순위대로 확인한다.
    """
    url = f"https://pypi.org/pypi/{package}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "anchor-license-audit"})
    with urllib.request.urlopen(request, timeout=PYPI_TIMEOUT_SECONDS) as response:
        info = json.load(response)["info"]

    version = info.get("version", "?")

    if expression := info.get("license_expression"):
        return LicenseRecord(package, version, expression, "license_expression")

    if declared := (info.get("license") or "").strip():
        # 일부 패키지는 라이선스 전문을 이 필드에 통째로 넣는다.
        return LicenseRecord(package, version, declared.splitlines()[0][:60], "license")

    for classifier in info.get("classifiers", []):
        if classifier.startswith("License ::"):
            return LicenseRecord(
                package, version, classifier.split("::")[-1].strip(), "classifier"
            )

    return LicenseRecord(package, version, "UNKNOWN", "none")


def check_version_floors() -> list[str]:
    """설치된 패키지가 라이선스상 요구되는 최소 버전을 만족하는지 확인한다."""
    from importlib.metadata import PackageNotFoundError, version as installed_version

    try:
        from packaging.version import Version
    except ImportError:
        print("경고: packaging 미설치. 버전 하한 검사를 건너뜁니다.", file=sys.stderr)
        return []

    violations: list[str] = []
    for package, floor, reason in VERSION_FLOORS:
        try:
            found = installed_version(package)
        except PackageNotFoundError:
            print(f"  {package:22} 미설치 — 건너뜀")
            continue

        if Version(found) < Version(floor):
            violations.append(f"{package} {found} < {floor} — {reason}")
            print(f"  {package:22} {found:12} ✗ 하한 {floor} 미달")
        else:
            print(f"  {package:22} {found:12} ✓ (하한 {floor})")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-on-copyleft",
        action="store_true",
        help="카피레프트 의존성 발견 시 종료 코드 1을 반환한다 (CI 게이트용)",
    )
    parser.add_argument(
        "--check-floors",
        action="store_true",
        help="설치된 패키지의 라이선스상 버전 하한을 검사한다",
    )
    args = parser.parse_args()

    print("=== 런타임 의존성 라이선스 ===")
    copyleft_found: list[str] = []
    unknown_found: list[str] = []

    for package in RUNTIME_PACKAGES:
        try:
            record = fetch_license(package)
        except urllib.error.URLError as error:
            print(f"{package:22} 조회 실패: {error}", file=sys.stderr)
            continue

        flag = ""
        if record.is_copyleft:
            flag = "  ← 카피레프트"
            copyleft_found.append(f"{record.package}: {record.license}")
        elif record.license == "UNKNOWN":
            flag = "  ← 미확인"
            unknown_found.append(record.package)

        print(
            f"{record.package:22} {record.version:12} "
            f"{record.license:36} ({record.source}){flag}"
        )

    if args.check_floors:
        print("\n=== 라이선스상 버전 하한 ===")
        floor_violations = check_version_floors()
    else:
        floor_violations = []

    problems = bool(copyleft_found or floor_violations)

    if copyleft_found:
        print("\n카피레프트 의존성이 감지되었습니다:", file=sys.stderr)
        for item in copyleft_found:
            print(f"  - {item}", file=sys.stderr)
        print("  THIRD-PARTY.md 및 docs/decisions/0002 를 확인하세요.", file=sys.stderr)

    if floor_violations:
        print("\n버전 하한 위반:", file=sys.stderr)
        for item in floor_violations:
            print(f"  - {item}", file=sys.stderr)

    if unknown_found:
        print(f"\n라이선스 미확인 {len(unknown_found)}건: {', '.join(unknown_found)}")
        print("저장소의 LICENSE 파일을 직접 확인해 THIRD-PARTY.md에 반영하세요.")

    if problems and args.fail_on_copyleft:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
