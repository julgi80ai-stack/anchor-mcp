# SPDX-License-Identifier: Apache-2.0
import uuid

from anchor.models import age_seconds, parse_iso, utcnow_iso, uuid7


def test_uuid7_is_valid_version_7():
    value = uuid.UUID(uuid7())
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_is_time_ordered():
    ids = [uuid7() for _ in range(10)]
    assert [u[:13] for u in ids] == sorted(u[:13] for u in ids)


def test_utcnow_iso_round_trips():
    now = utcnow_iso()
    assert now.endswith("Z")
    assert age_seconds(now) < 5
    assert parse_iso(now).tzinfo is not None
