import pytest

from src.openapi.file_detection import touches_openapi_source
from src.openapi.spec_loader import SpecLoader


@pytest.mark.parametrize(
    ("changed_file", "expected"),
    [
        ("docs/_index.yaml", True),
        ("docs/common/errors.yaml", True),
        ("docs/exchanges/users/get-profile.yaml", True),
        (r"docs\common\responses.yaml", True),
        ("docs-old/exchanges/get-profile.yaml", False),
        ("src/openapi/generated.py", True),  # legacy marker fallback
        ("README.md", False),
    ],
)
def test_detects_changes_in_split_openapi_source(changed_file, expected):
    assert touches_openapi_source([changed_file], "./docs/") is expected


def test_split_openapi_spec_is_resolved_from_configured_index():
    files = {
        "docs/_index.yaml": b"""\
openapi: 3.0.3
info: {title: Split API, version: 1.0.0}
paths:
  /user/profile:
    $ref: ./exchanges/get-user-profile.yaml
""",
        "docs/exchanges/get-user-profile.yaml": b"""\
get:
  operationId: getUserProfile
  responses:
    '200':
      $ref: ../common/responses.yaml#/Ok
""",
        "docs/common/responses.yaml": b"""\
Ok:
  description: OK
  content:
    application/json:
      schema:
        type: object
        properties:
          name: {type: string}
""",
    }

    loader = SpecLoader()
    spec = loader.load_spec_from_files(files, ("_index.yaml",))

    assert spec is not None
    assert loader._count_unresolved_refs(spec) == 0
    response = spec["paths"]["/user/profile"]["get"]["responses"]["200"]
    assert response["description"] == "OK"
