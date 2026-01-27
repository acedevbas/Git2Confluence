# OpenAPI History Tracker - AI Coding Instructions

## Project Overview

This tool tracks **historical changes to a specific OpenAPI endpoint** across GitLab merge requests. It fetches MR history, downloads repository snapshots, resolves OpenAPI specs (with `$ref` expansion), extracts endpoint schemas, and generates a markdown change log.

**Key Data Flow:**
```
GitLab MRs → Download Snapshot (zip) → Extract OpenAPI YAML → Resolve $refs (Prance) → Extract Endpoint Schema → Diff Against Previous → Generate Report
```

## Architecture & Key Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **REST API** | `api/app.py` → FastAPI | REST API for documentation generation |
| **API Entry** | `run_api.py` | Uvicorn server entry point |
| **Turbo Entry (Primary)** | `main_turbo.py` → `TurboHistoryBuilder` | Optimized: merge_commit_sha, two-level cache, REVERT filtering |
| **Entry Point** | `main.py` → `HistoryBuilder` | Orchestrates the full pipeline (single-threaded) |
| **Parallel Entry** | `main_parallel.py` → `ParallelHistoryBuilder` | Multi-threaded version with ThreadPoolExecutor |
| **GitLab Client** | `gitlab_client_wrapper.py` | Fetches MRs, filters API-related, downloads repo archives |
| **Spec Loader** | `spec_loader.py` | Finds & parses OpenAPI YAML/JSON with fallback strategies |
| **Schema Extractor** | `schema_extractor.py` | Extracts simplified endpoint schema (params, body, responses) |
| **Diff Engine** | `diff_engine.py` | Compares specs using `DeepDiff` with cosmetic field exclusions |
| **Regression Filter** | `regression_filter.py` | Filters temporary regressions (grace period pattern) |
| **Redis Cache** | `redis_cache.py` | Two-level cache: specs (gzip+base64) + schemas |
| **File Cache** | `cache_manager.py` | Alternative JSON file-based cache in `cache_snapshots/` |
| **Confluence Publisher** | `confluence_publisher.py` | Publishes change history to Confluence pages |

**Legacy/Alternative Implementations:** `history_provider.py`, `gitlab_fetcher.py`, `snapshot_engine.py` - older implementations, prefer `main_turbo.py` flow.

## REST API

### Running the API Server
```bash
python run_api.py --port 8000 --reload
```

### Authentication
All API endpoints require `X-API-Key` header with the service API key from `.env`:
```
SERVICE_API_KEY=b8299583-cba7-4dbd-9f7d-27c72a9a4415
```

### Endpoints

#### POST /api/v1/documentation/generate
Generate and publish API documentation to Confluence.

**Request:**
```json
{
    "target_endpoint": "POST /orders/products/return",
    "confluence_parent_page_id": "169804502",
    "confluence_token": "your-confluence-token",
    "confluence_space_key": "pickup"
}
```

**Response:**
```json
{
    "success": true,
    "page_id": "439913250",
    "page_url": "https://kb.vseinstrumenti.ru/pages/viewpage.action?pageId=439913250",
    "events_count": 6,
    "processing_time_sec": 6.0,
    "events": [...]
}
```

#### GET /health
Health check endpoint (no auth required).

### API Files Structure
```
api/
├── __init__.py
├── app.py           # FastAPI application
├── deps.py          # Dependencies (API key auth)
├── schemas.py       # Pydantic request/response models
└── routes/
    ├── __init__.py
    └── docs.py      # Documentation generation endpoint
```

### API Features
- **Regression Filtering**: Temporary regressions (field removed + restored within grace_period) are automatically filtered
- **REVERT Pairs Filtering**: REVERT MRs that delete then restore endpoint within grace_period are filtered
- **Cosmetic Fields Exclusion**: Description, summary, validation hints excluded from diffs
- **Two-Level Cache**: Uses Redis cache for specs and schemas (same as main_turbo.py)

## Critical Patterns

### OpenAPI Spec Loading (Validation-Free Approach)
`spec_loader.py` uses **Prance's ResolvingParser** with `strict=False` to fully resolve all `$ref` references while tolerating minor spec validation issues.

```python
# ✅ Correct - ResolvingParser with strict=False for full $ref resolution
from prance import ResolvingParser
parser = ResolvingParser(file_path, strict=False)
spec = parser.specification  # All $refs are resolved inline

# ❌ Avoid - RefResolver doesn't fully resolve nested $refs
from prance.util.resolver import RefResolver
resolver = RefResolver(specs, url=file_path)  # Incomplete resolution
```

**Why ResolvingParser matters:** Without full resolution, changes to referenced schemas (e.g., `returnCommodity` inside `products.items`) won't be detected in diffs.

### Cosmetic Field Exclusions (DeepDiff)
`main_turbo.py` uses `exclude_regex_paths` to ignore documentation-only changes:

```python
EXCLUDE_COSMETIC_FIELDS = [
    # Documentation fields (pure text, no API impact)
    re.compile(r"\['description'\]"),
    re.compile(r"\['summary'\]"),
    re.compile(r"\['title'\]"),
    re.compile(r"\['externalDocs'\]"),
    re.compile(r"\['deprecated'\]"),
    re.compile(r"\['tags'\]"),
    re.compile(r"\['operationId'\]"),
    # Validation hints (don't change API contract)
    re.compile(r"\['minItems'\]"),
    re.compile(r"\['maxItems'\]"),
    re.compile(r"\['minLength'\]"),
    re.compile(r"\['maxLength'\]"),
    re.compile(r"\['minimum'\]"),
    re.compile(r"\['maximum'\]"),
    re.compile(r"\['pattern'\]"),
    re.compile(r"\['default'\]"),
    re.compile(r"\['x-.*'\]"),  # Extension fields
    # NOTE: 'format' and 'example' are NOT excluded - they indicate meaningful changes
]

diff = DeepDiff(old_schema, new_schema, ignore_order=True, exclude_regex_paths=EXCLUDE_COSMETIC_FIELDS)
```

### OpenAPI File Discovery
`spec_loader.py._find_openapi_file()` searches with strict priority order:
1. `api/swagger/openapi.yaml` (current standard)
2. `api/swagger/api.yaml` (legacy naming in older commits)
3. `openapi.yaml`, `swagger.yaml` (root-level)

**Critical:** Excludes hidden directories (`.catalog/`, `.gitlab/`) and `vendor/` to avoid matching non-OpenAPI files like Backstage catalogs.

### Schema Extraction
`SchemaExtractor.extract()` returns a normalized dict structure:
```python
{
    "summary": str,
    "parameters": {"header": {}, "query": {}, "path": {}},
    "requestBody": {},
    "responses": {"200": {"description": str, "schema": {}}}
}
```
Supports both **Swagger 2.0** (`in: body` params) and **OpenAPI 3.0** (`requestBody` object).

### Two-Level Redis Cache
`redis_cache.py` implements two-level caching for maximum performance:

| Level | Key Format | Content | Purpose |
|-------|-----------|---------|---------|
| **Spec** | `openapi:spec:{sha16}` | Full resolved OpenAPI spec (gzip+base64) | Reuse across ALL endpoints |
| **Schema** | `openapi:schema:{sha8}:{endpoint_hash8}` | Extracted endpoint schema | Fast comparison |

**Storage format for specs:** `gzip:` prefix + base64-encoded gzip data (compatible with `decode_responses=True`).

**Lookup order:**
1. Schema cached? → Use directly (CACHE)
2. Spec cached? → Extract schema, cache it (SPEC)
3. Nothing cached? → Download ZIP, resolve, cache both (DOWNLOAD)

**Performance impact:**
- First run: ~30-60 min (downloads all 242 archives)
- Same endpoint again: **~2 seconds** (schema cache hit)
- Different endpoint: **~2-4 seconds** (extract from cached specs, no download)

Uses `orjson` for fast JSON serialization with native datetime support.

### MR Snapshot Strategy (merge_commit_sha vs head_sha)
**Critical:** Use `mr.merge_commit_sha` for snapshot downloads, NOT `mr.sha` (head_sha).

```python
# ✅ Correct for history tracking - shows actual state AFTER merge
merge_commit_sha = mr.merge_commit_sha  # Includes all prior changes from master

# ❌ Wrong for history - MR branch may be forked from OLD master
head_sha = mr.sha  # May NOT contain changes from other MRs merged before!
```

**Why merge_commit_sha:**
- Shows the actual repository state AFTER the MR was merged
- Includes all changes from master that were merged before this MR
- Prevents false "DELETED" events when MR was forked before endpoint creation

**Handling batch merges (same merge_commit_sha):**
- Deduplicate MRs by merge_commit_sha before processing
- Keep first MR in chronological order (by merged_at, iid tiebreaker)

```python
# Deduplicate by merge_commit_sha
seen_commits = set()
deduped_mrs = []
for mr in sorted_mrs:
    if mr['commit_sha'] not in seen_commits:
        seen_commits.add(mr['commit_sha'])
        deduped_mrs.append(mr)
```

### MR Sorting (Batch Merge Handling)
MRs merged in batches have nearly identical `merged_at` timestamps (sub-second differences). Sort by `(date[:19], iid)` to ensure correct chronological order:

```python
# Truncate to seconds, use IID as tiebreaker for batch merges
sorted_mrs = sorted(all_mrs, key=lambda x: (x['date'][:19], x['mr_iid']))
```

### Task ID Extraction & REVERT Detection
`_extract_task_id()` normalizes JIRA task IDs and detects REVERT MRs:

```python
# Supported formats:
"LOGRETAIL-1168"           → "LOGRETAIL-1168"  # Standard
"logretail-1168"           → "LOGRETAIL-1168"  # Lowercase
"Feature/logretail 1168"   → "LOGRETAIL-1168"  # Space separator
"feature/LOGRETAIL-1168"   → "LOGRETAIL-1168"  # From branch name

# REVERT detection:
"Revert \"Merge branch...\"" → "REVERT"  # Title starts with Revert
"revert-67f55a83"            → "REVERT"  # Branch starts with revert-
```

**REVERT pairs filtering:** If a REVERT MR deletes an endpoint and another REVERT restores it within grace period, both are filtered out.

### Regression Filtering
Two types of temporary regressions are filtered:

1. **Field-level regressions** (`regression_filter.py`):
   - Field removed in MR A, restored in MR B within grace period
   - Example: `electronicReceipt` removed in !696, restored in !725 (5 days)

2. **REVERT pairs** (`_filter_revert_pairs()`):
   - REVERT MR deletes endpoint, another REVERT restores it
   - Example: !25 (DELETED) + !29 (CREATED) within 2 days

## Configuration

All settings in `config.py` via `pydantic-settings`. Key settings:
```python
target_endpoint = "POST /orders/products/return"  # Format: "METHOD /path"
mr_limit = 1000
flush_cache_on_start = False  # Set False to preserve spec cache between runs
gitlab_ssl_verify = False  # For internal GitLab with self-signed certs
grace_period_days = 7  # For regression filtering

# Confluence Integration
confluence_base_url = "https://kb.vseinstrumenti.ru"
confluence_space_key = "AIDOCS"  # Must be a valid space key
publish_to_confluence = True  # Enable auto-publish after analysis
```

**SSL Warnings:** Suppressed in `main_turbo.py` via `urllib3.disable_warnings()` for internal GitLab servers.

Credentials are in `config.py` - **never commit production tokens**. Use `.env` file for secrets.

## Confluence Integration

The project includes automatic publishing of change history to Confluence pages.

### Key Files
- `confluence_publisher.py` - Main Confluence integration module
- Uses `atlassian-python-api` library for Confluence Server/Data Center

### Usage

```python
# Automatic: Set in config.py or .env
publish_to_confluence = True

# Manual: Use ConfluencePublisher directly
from confluence_publisher import publish_to_confluence

result = publish_to_confluence(
    method="POST",
    path="/orders/products",
    events=history_events,  # From TurboHistoryBuilder
    space_key="AIDOCS"
)
print(result)  # {'success': True, 'page_id': '441321527', 'page_url': '...'}
```

### Configuration via .env
```bash
CONFLUENCE_BASE_URL=https://kb.vseinstrumenti.ru
CONFLUENCE_TOKEN=your-personal-access-token
CONFLUENCE_SPACE_KEY=AIDOCS
PUBLISH_TO_CONFLUENCE=true
```

### Features
- Creates or updates pages automatically (uses CQL to find existing)
- Generates Confluence Storage Format (XHTML-like)
- HTTP method badges (color-coded: GET=Blue, POST=Green, etc.)
- Change history table with JIRA links
- Expandable diff details section
- Row highlighting for CREATED/MODIFIED/DELETED events

### Testing Confluence Connection
```bash
# Run standalone test
python confluence_publisher.py

# Check available spaces
# Will list spaces if configured space not found
```

### Troubleshooting
- **Space not found**: Run `confluence_publisher.py` to see available spaces
- **Token issues**: Use Personal Access Token, not basic auth
- **SSL errors**: Confluence uses HTTPS, ensure certificates are valid

## Running & Debugging

```bash
# Turbo execution (RECOMMENDED - fastest, uses merge_commit_sha + two-level cache)
python main_turbo.py

# Main execution (single-threaded, legacy)
python main.py

# Parallel execution (faster, 10 workers default)
python main_parallel.py

# Debug specific MR issues (numerous debug_*.py scripts exist)
python debug_1141.py  # Example: Analyze specific MR for endpoint changes
```

**Changing endpoint:** Edit `config.py` → `target_endpoint`, then run. Specs are cached, so new endpoint analysis takes ~2-4 seconds.

Debug scripts (`debug_*.py`) are investigation tools for specific issues - examine them to understand past problems with parsing, filtering, or diff detection.

## Common Issues & Solutions

1. **Duplicate YAML keys** → Handled by `spec_loader.py` with `ruamel.yaml` pre-cleaning before Prance
2. **MR not detected as API-related** → Check `_is_mr_relevant()` patterns in `gitlab_client_wrapper.py`
3. **False positive changes (cosmetic)** → Excluded via `EXCLUDE_COSMETIC_FIELDS` regex patterns in `main_turbo.py`
4. **Redis gzip decode error** → Fixed: specs stored as `gzip:` + base64 (compatible with `decode_responses=True`)
5. **False "DELETED" events** → Use `merge_commit_sha` not `head_sha` for snapshots (fixed in turbo)
6. **REVERT MRs attributed to wrong task** → Fixed: REVERT detection in `_extract_task_id()`
7. **Parse failures on old MRs** → Likely legacy file naming (`api.yaml` vs `openapi.yaml`); verify `priority_patterns` in spec_loader
8. **Slow performance** → Use `main_turbo.py` with Redis cache; first run downloads, subsequent runs use cache
9. **Timezone comparison errors** → Fixed: use `datetime.now(timezone.utc)` in regression_filter.py
10. **Schema not fully expanded (allOf)** → Fixed in `flatten_schema_to_fields`: merges allOf + properties, expands nested items

## File Organization

- `cache_snapshots/` - Persistent JSON cache files (commit_method_hash.json)
- `temp_snapshots/`, `temp_processing/` - Working directories (auto-cleaned)
- `debug_temp*/` - Debug session artifacts
- `test_*.py` - Unit tests for specific components

## Performance Benchmarks

| Scenario | Time | Speed |
|----------|------|-------|
| First run (cold cache) | ~50 min | ~0.1 MR/s |
| Same endpoint (schema cache) | ~2 sec | ~120 MR/s |
| Different endpoint (spec cache) | ~3 sec | ~80 MR/s |
| Cache stats check | instant | N/A |

**Cache size:** ~242 specs × ~200KB (gzip+base64) ≈ 50MB in Redis

## Confluence Template Generator

### Key Files
- `confluence_template_generator.py` - Generates Confluence Storage Format HTML
- Uses ui-tabs macro for version history by JIRA task

### Schema Flattening (`flatten_schema_to_fields`)
Recursively extracts fields from OpenAPI schema for table display.

**Key behaviors:**
1. **allOf + properties merging**: When schema has both `allOf` and `properties`, both are processed. Properties with same name are merged (preferring the one with more detail like `items` or nested `properties`).
2. **Array items expansion**: For `type: array`, recursively processes `items` to extract nested fields.
3. **Nested objects**: Objects with `properties` are recursively expanded with `level` tracking.
4. **Level-based indentation**: Fields have `level` attribute (0=top, 1=nested, 2=deeply nested). Used for visual indentation in tables.

**Example input:**
```json
{
  "allOf": [
    {"properties": {"result": {"type": "object"}}},
    {"properties": {"result": {"type": "array", "items": {"properties": {"guid": {...}}}}}}
  ]
}
```

**Example output:**
```python
[
  {"key": "result", "type": "array", "level": 0, "full_path": "result"},
  {"nested_key": "guid", "type": "string", "level": 1, "full_path": "result.guid"}
]
```

**Important**: The merging logic prefers the schema with more detail (`items` or `properties`) over the bare `type: object` placeholder.

### Date Sources
The **date shown in change history** comes from GitLab MR's `merged_at` field:
```python
# main_turbo.py line 173
'date': mr.merged_at  # ISO 8601 format: "2025-08-06T14:23:45.000Z"
```

**Important:** This is the date when MR was **merged** (влит в основную ветку), NOT when it was created. This reflects when changes actually appeared in production API.

### Field Change Highlighting
Three highlight colors for changed fields in tables:
```python
HIGHLIGHT_COLORS = {
    'added': 'rgb(212,237,218)',      # Green - new field
    'modified': 'rgb(255,243,205)',   # Yellow - changed field  
    'removed': 'rgb(248,215,218)'     # Red - removed field
}
```

**Important**: `required` attribute changes (True→False or False→True) are always marked as `'modified'` (yellow), not `'added'` (green). This applies to:
- Header/query parameters where `required` is a boolean
- Object properties where field is added/removed from `required` array
- DeepDiff paths ending with `['required']` in `dictionary_item_added`/`dictionary_item_removed`

**Detection logic in `confluence_publisher.py`:**
```python
# In _extract_field_changes():
if path.endswith("['required']"):
    changes[normalized] = 'modified'  # Not 'added' or 'removed'!
```

**Detection logic in `confluence_template_generator.py`:**
```python
# In _generate_detailed_change_description():
if path.endswith("['required']"):
    sections[section]['modified'].append(f"{field} (стал обязательным)")
# NOT sections[section]['added'].append(field)
```

### Change Description Generation
`_generate_detailed_change_description()` analyzes DeepDiff output and generates human-readable descriptions grouped by section:

```python
# Example output:
"Тело запроса (requestBody): добавлены поля country, gtd, saleGuid"
"Ответ 403: добавлен новый код ответа"
"Тело запроса (requestBody): поле guid стало опциональным"
```

### allOf/anyOf Restructuring Handling
When OpenAPI schema restructures `allOf` (e.g., merges 2 items into 1), fields from removed items should NOT be marked as "added":

```python
# Collect fields from removed allOf items (they existed before)
previously_existing_fields = set()
for path, value in diff.get('iterable_item_removed', {}).items():
    if 'allOf' in path or 'anyOf' in path or 'oneOf' in path:
        if isinstance(value, dict):
            props = value.get('properties', {})
            previously_existing_fields.update(props.keys())

# Exclude from "added" list
added_props = new_props - old_props - previously_existing_fields
```

### Jira Macro Format
For linking to JIRA tasks in Confluence:
```xml
<ac:structured-macro ac:name="jira">
  <ac:parameter ac:name="server">Jira Software</ac:parameter>
  <ac:parameter ac:name="serverId">f07fdead-9301-3f71-bd8f-d9ef673b9368</ac:parameter>
  <ac:parameter ac:name="key">LOGRETAIL-1870</ac:parameter>
</ac:structured-macro>
```

### Content-Type Header Exclusion
The `Content-Type` header is NOT shown in header parameters table (handled in `confluence_publisher.py`):
```python
# Skip Content-Type - it's always application/json and clutters the table
if param_name.lower() == 'content-type':
    continue
```

## Key Data Structures

### History Event
```python
{
    'event_type': 'CREATED' | 'MODIFIED' | 'DELETED',
    'task_id': 'LOGRETAIL-1870',
    'mr_iid': 623,
    'author': 'Карачёв Никита Юрьевич',
    'merged_at': datetime(2025, 8, 6, 14, 23, 45),
    'diff': {...}  # DeepDiff result, None for CREATED
}
```

### Field Changes Dictionary
```python
field_changes = {
    'requestBody.products.country': 'added',
    'requestBody.products.guid': 'modified',
    'responses.403': 'added'
}
```

## Schema Quality & Filtering

### Poor Schema Detection
Legacy OpenAPI specs (before ~2024) often have incomplete response schemas where `result` is defined as `{type: object}` without nested `properties`. The `_is_schema_poor()` method in `confluence_publisher.py` detects these:

```python
def _is_schema_poor(self, response_schema: Dict[str, Any]) -> bool:
    """
    Poor schema patterns:
    - result: {type: object} without properties, items, or allOf
    - result: {type: array} without items
    """
    # Check for result without details
    result = props.get('result', {})
    if result.get('type') == 'object':
        if 'properties' not in result and 'allOf' not in result:
            return True  # Poor schema
```

### Event Filtering Strategy
Events are filtered/annotated based on schema quality and change significance:

| Condition | Action | Display in History Block |
|-----------|--------|-------------------------|
| Poor schema + CREATED | Mark `_skip_reason='poor_schema'` | "Эндпоинт создан" |
| Poor schema + DELETED | Mark `_skip_reason='poor_schema'` | "Эндпоинт удалён" |
| Poor schema + MODIFIED | Mark `_skip_reason='poor_schema'`, skip tab | "Детализация контракта недоступна" |
| Previous schema was poor | Don't show diff highlighting | Normal tab, no yellow highlights |
| All changes insignificant | Mark `_skip_reason='insignificant'`, skip tab | Not shown |

### Insignificant Changes Filtering
The `_is_insignificant_change()` method filters out minor format changes that don't affect API contract:

```python
# Insignificant format changes (same base type):
# int8 ↔ uint8 ↔ int16 ↔ int32 ↔ int64 → SKIP
# float ↔ double → SKIP

# Significant type changes:
# integer → string → SHOW (real contract change)
```

**Filtered changes:**
- Integer format variations: `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `int64`, `uint64`
- Float format variations: `float`, `double`
- Similar base types: `integer` ↔ `number`

### False Positive Filtering
The `_filter_false_positive_changes()` method prevents incorrect "added/removed" annotations when schema structure changes (e.g., `allOf` flattening):

```python
# If field marked as 'removed' but field NAME exists in current schema → FALSE POSITIVE
# If field marked as 'added' but field NAME existed in previous schema → FALSE POSITIVE

# Example: allOf restructuring
# Before: allOf[1].properties.result.items.properties.guid
# After: properties.result.items.properties.guid
# DeepDiff sees: guid REMOVED + guid ADDED
# Filter sees: guid exists in both → skip both changes
```

### Structural Changes Filtering
Changes to schema composition elements (`allOf`, `anyOf`, `oneOf`) are filtered as they represent internal restructuring, not API changes:

```python
# Skip paths ending with allOf/anyOf/oneOf in dictionary_item_removed
if path.endswith("['allOf']") or path.endswith("['anyOf']") or path.endswith("['oneOf']"):
    continue  # Internal restructuring, not a real change
```

## Event Metadata

Events passed to `generate_full_page()` may contain metadata fields:

```python
{
    'task_id': 'LOGRETAIL-283',
    'date': '2024-04-22',
    'type': 'MODIFIED',
    'diff': {...},
    'schema': {...},
    'previous_schema': {...},
    # Metadata added by confluence_publisher:
    '_skip_reason': 'poor_schema' | 'insignificant' | None
}
```

**`_skip_reason` values:**
- `'poor_schema'` - Event has incomplete OpenAPI spec, tab not generated but shown in history
- `'insignificant'` - All changes were minor (format-only), not shown anywhere
- `None` - Normal event with full details

