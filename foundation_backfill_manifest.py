"""Version 1 evidence-only manifests. No allocations, credentials or recipient data.

All lists are unordered sets for canonicalization (duplicates are still refused).
Symbolic season/club keys and alias digests are SHA256 strings, not allocated IDs.
Evidence references identify separately reviewed artifacts; this validator cannot
prove their truth, authorization, or coverage of an actual database.
"""
import hashlib
import json
from pathlib import Path
import re

VERSION = 1
KINDS = {
    "player": {"player_id", "email", "claimed_identity", "favorite_club",
               "platform_admin", "role", "recap_opt_in"},
    "season": {"season_id", "football_season_key"},
    "week": {"week_id", "season_id", "football_season_key", "number"},
    "fixture": {"fixture_id", "week_id", "football_season_key", "round",
                "home_club_key", "away_club_key"},
    "alias": {"football_season_key", "normalized_name_sha256", "club_key"},
    "pick": {"pick_id", "fixture_id", "selected_club_key"},
    "preference": {"preference_id", "fixture_id", "selected_club_key"},
    "task": {"week_id", "status", "has_claim"},
    "recipient": {"recipient_kind", "permission_evidence", "identity_evidence"},
}
IDENTITY_FIELDS = KINDS["player"] - {"player_id"}
EVIDENCE_KINDS = {"fixture_provider", "season_provider", "reviewed_alias",
                  "participation", "permission", "task_disposition", "product_decision"}
HEX = re.compile(r"[0-9a-f]{64}")
MAX_BYTES = 2 * 1024 * 1024


class ManifestError(ValueError):
    """Fixed reason code only; never includes submitted values."""


def _need(condition, code="MALFORMED_MANIFEST"):
    if not condition:
        raise ManifestError(code)


def _fields(value, expected):
    _need(type(value) is dict)
    _need(set(value) == set(expected), "UNRECOGNIZED_OR_MISSING_FIELDS")


def _hash(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def _normalize(value):
    if type(value) is dict:
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if type(value) is list:
        return sorted((_normalize(v) for v in value), key=lambda v: json.dumps(
            v, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return value


def canonical_json(value):
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def manifest_checksum(document):
    """Checksum covers every field except checksum itself; input is not mutated."""
    return hashlib.sha256(canonical_json(
        {k: v for k, v in document.items() if k != "checksum"}).encode()).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def read_manifest(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        _need(len(raw) <= MAX_BYTES, "MANIFEST_TOO_LARGE")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: _need(False, "INVALID_JSON"))
    except ManifestError:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        raise ManifestError("MANIFEST_UNREADABLE_OR_INVALID_JSON") from None
    return document


def load_manifest(path):
    return validate_manifest(read_manifest(path))


def validate_manifest(document):
    """Validate shape, checksums and internal evidence consistency, never readiness."""
    if type(document) is dict and document.get("schema_version") == 2:
        return validate_planning_manifest(document)
    _fields(document, {"schema_version", "source", "evidence", "mappings", "checksum"})
    _need(type(document["schema_version"]) is int and document["schema_version"] == VERSION,
          "UNSUPPORTED_MANIFEST_VERSION")
    _fields(document["source"], {"schema_sha256", "data_sha256"})
    _need(all(_hash(v) for v in document["source"].values()))
    _need(type(document["evidence"]) is list and type(document["mappings"]) is list)
    _need(len(document["evidence"]) <= 10000 and len(document["mappings"]) <= 10000,
          "MANIFEST_TOO_LARGE")
    evidence = {}
    blockers = set()

    def block(code):
        blockers.add(code)

    for item in document["evidence"]:
        _fields(item, {"sha256", "kind"})
        _need(_hash(item["sha256"]) and type(item["kind"]) is str
              and item["kind"] in EVIDENCE_KINDS)
        if item["sha256"] in evidence:
            block("DUPLICATE_OR_CONFLICTING_EVIDENCE")
        evidence[item["sha256"]] = min(evidence.get(item["sha256"], item["kind"]),
                                       item["kind"])

    rows = document["mappings"]
    indexed = {kind: {} for kind in KINDS}
    for row in rows:
        _need(type(row) is dict and type(row.get("kind")) is str
              and row["kind"] in KINDS, "UNKNOWN_MAPPING_KIND")
        kind = row["kind"]
        _fields(row, KINDS[kind] | {"kind", "evidence"})
        _need(type(row["evidence"]) is list and all(_hash(v) for v in row["evidence"]))
        if not row["evidence"]:
            block("MISSING_EVIDENCE")
        if len(set(row["evidence"])) != len(row["evidence"]):
            block("DUPLICATE_EVIDENCE_REFERENCE")
        if set(row["evidence"]) - evidence.keys():
            block("MISSING_EVIDENCE_REFERENCE")
        for field in sorted(KINDS[kind]):
            value = row[field]
            if field in IDENTITY_FIELDS and kind == "player":
                _need(value is None, "IDENTITY_PERMISSION_VALUES_UNSUPPORTED_IN_V1")
            elif field.endswith("_id") or field in {"number", "round"}:
                _need(type(value) is int and value > 0)
            elif field.endswith("_key") or field.endswith("_sha256") or field.endswith("_evidence"):
                _need(value is None or _hash(value))
                if value is None and field != "identity_evidence":
                    block("MISSING_MAPPING_EVIDENCE")
            elif field == "has_claim":
                _need(type(value) is bool)
            elif field == "status":
                _need(type(value) is str and value in {
                    "pending", "running", "completed", "failed", "uncertain"})
            elif field == "recipient_kind":
                _need(value in ("recap", "admin_alert"))

        key_field = {"player": "player_id", "season": "season_id", "week": "week_id",
                     "fixture": "fixture_id", "pick": "pick_id", "preference": "preference_id",
                     "task": "week_id", "recipient": "recipient_kind"}.get(kind)
        key = row[key_field] if key_field else (row["football_season_key"], row["normalized_name_sha256"])
        if key in indexed[kind]:
            block("AMBIGUOUS_CLUB_ALIAS" if kind == "alias" else "DUPLICATE_OR_CONFLICTING_MAPPING")
            indexed[kind][key] = min((indexed[kind][key], row), key=canonical_json)
        else:
            indexed[kind][key] = row

    if not rows:
        block("MISSING_MAPPINGS")
    for row in rows:
        kind = row["kind"]
        if kind == "week":
            parent = indexed["season"].get(row["season_id"])
            if parent is None:
                block("MISSING_SEASON_MAPPING")
            elif row["football_season_key"] != parent["football_season_key"]:
                block("INCONSISTENT_SEASON_EVIDENCE")
        elif kind == "fixture":
            parent = indexed["week"].get(row["week_id"])
            if parent is None:
                block("MISSING_WEEK_MAPPING")
            elif (row["football_season_key"], row["round"]) != (
                    parent["football_season_key"], parent["number"]):
                block("INCONSISTENT_SEASON_ROUND_EVIDENCE")
            if row["home_club_key"] == row["away_club_key"]:
                block("CONFLICTING_FIXTURE_PARTICIPANTS")
        elif kind in {"pick", "preference"}:
            fixture = indexed["fixture"].get(row["fixture_id"])
            selected = row["selected_club_key"]
            if fixture is None or selected is None or selected not in (
                    fixture["home_club_key"], fixture["away_club_key"]):
                block("UNRESOLVED_PICK_PARTICIPANT")
        elif kind == "alias":
            season_keys = {r["football_season_key"] for r in indexed["season"].values()}
            participants = {r[f] for r in indexed["fixture"].values()
                            if r["football_season_key"] == row["football_season_key"]
                            for f in ("home_club_key", "away_club_key")}
            if row["football_season_key"] not in season_keys:
                block("MISSING_SEASON_MAPPING")
            if row["club_key"] is None or row["club_key"] not in participants:
                block("UNRESOLVED_CLUB_ALIAS")
        elif kind == "task":
            if row["week_id"] not in indexed["week"]:
                block("MISSING_WEEK_MAPPING")
            if row["has_claim"] or row["status"] in {"running", "uncertain"}:
                block("UNSUPPORTED_OR_IN_FLIGHT_TASK")
        elif kind == "recipient":
            permission = row["permission_evidence"]
            if permission not in row["evidence"] or evidence.get(permission) != "permission":
                block("RECIPIENT_PERMISSION_UNRESOLVED")
            identity = row["identity_evidence"]
            if identity is not None:
                block("RECIPIENT_IDENTITY_UNSUPPORTED_IN_V1")

    # One league cannot silently collapse multiple league seasons/rounds.
    for kind, fields in (("season", ("football_season_key",)),
                         ("week", ("football_season_key", "number")),
                         ("fixture", ("football_season_key", "round", "home_club_key", "away_club_key"))):
        keys = [tuple(row[f] for f in fields) for row in indexed[kind].values()]
        if len(keys) != len(set(keys)):
            block("DUPLICATE_CANONICAL_MAPPING")
    _need(_hash(document["checksum"]), "INVALID_MANIFEST_CHECKSUM")
    _need(document["checksum"] == manifest_checksum(document), "MANIFEST_CHECKSUM_MISMATCH")
    return {"schema_version": VERSION, "status": "blocked" if blockers else "valid",
            "scope": "structure_and_internal_consistency_only",
            "execution_authorized": False, "checksum": document["checksum"],
            "mapping_counts": {k: len(v) for k, v in sorted(indexed.items())},
            "blockers": [{"label": "Unresolved", "code": c} for c in sorted(blockers)]}

# Version 2 contains private reviewed planning values. Never emit these in CLI
# diagnostics. Evidence hashes refer to separately reviewed artifacts, not proof
# that an arbitrary submitted assertion is true. V1 remains evidence-only.
PLAN_FIELDS = {
    'policy': {'timestamp', 'slug', 'status', 'timezone', 'correspondent_enabled', 'user_is_platform_admin', 'club_is_active', 'sync_enabled'},
    'competition': {'key', 'code', 'name', 'providers'},
    'football_season': {'key', 'competition_key', 'name', 'start_year', 'providers'},
    'club': {'key', 'name', 'providers'},
    'season': {'id', 'football_season_key', 'api_competition_code', 'api_season_year'},
    'week': {'id', 'season_id', 'football_season_key', 'number'},
    'fixture': {'id', 'week_id', 'football_season_key', 'round', 'home', 'away',
                'home_club_key', 'away_club_key', 'external_match_id', 'providers'},
    'alias': {'football_season_key', 'name', 'club_key'},
    'recipient': {'recipient_kind', 'email_normalized', 'enabled', 'user_id'},
}
PLAN_EVIDENCE = {'policy': 'product_decision', 'competition': 'season_provider',
                 'football_season': 'season_provider', 'club': 'reviewed_alias',
                 'season': 'season_provider', 'week': 'fixture_provider',
                 'fixture': 'fixture_provider', 'alias': 'reviewed_alias', 'recipient': 'permission'}


def validate_planning_manifest(document):
    """Strict private v2 shape; source binding/domain resolution happens in planner.

    No identity associations or elevated permissions supported in this slice.
    Explicit reviewed policy is required even for nonprivileged required values.
    """
    from datetime import datetime
    _fields(document, {'schema_version', 'source', 'evidence', 'mappings', 'checksum'})
    _need(type(document['schema_version']) is int and document['schema_version'] == 2)
    _fields(document['source'], {'schema_sha256', 'data_sha256'})
    _need(all(_hash(v) for v in document['source'].values()))
    _need(type(document['evidence']) is list and type(document['mappings']) is list)
    _need(len(document['evidence']) <= 10000 and len(document['mappings']) <= 10000, 'MANIFEST_TOO_LARGE')
    evidence = {}
    blockers = set()
    for e in document['evidence']:
        _fields(e, {'sha256', 'kind'})
        _need(_hash(e['sha256']) and type(e['kind']) is str and e['kind'] in EVIDENCE_KINDS)
        if e['sha256'] in evidence: blockers.add('DUPLICATE_OR_CONFLICTING_EVIDENCE')
        evidence[e['sha256']] = min(evidence.get(e['sha256'], e['kind']), e['kind'])
    counts = {}
    for row in document['mappings']:
        _need(type(row) is dict and type(row.get('kind')) is str and row['kind'] in PLAN_FIELDS)
        kind = row['kind']
        _fields(row, PLAN_FIELDS[kind] | {'kind', 'evidence'})
        refs = row['evidence']
        _need(type(refs) is list and all(_hash(r) for r in refs))
        if not refs or len(set(refs)) != len(refs) or any(r not in evidence for r in refs):
            blockers.add('MISSING_OR_DUPLICATE_EVIDENCE')
        if PLAN_EVIDENCE[kind] not in {evidence.get(r) for r in refs}:
            blockers.add('REVIEWED_EVIDENCE_KIND_REQUIRED')
        counts[kind] = counts.get(kind, 0) + 1
        for field in PLAN_FIELDS[kind]:
            value = row[field]
            if field == 'providers':
                _need(type(value) is list)
                fields = {'provider', 'external_id'}
                if kind == 'fixture':
                    fields |= {'home_external_id', 'away_external_id', 'external_season_id', 'external_competition_id'}
                for provider in value:
                    # Legacy football-data fixtures persisted the fixture ID only.
                    # A minimal assertion does not invent team/season-object IDs.
                    minimal = (kind == 'fixture' and type(provider) is dict and
                               provider.get('provider') == 'football-data' and
                               set(provider) == {'provider', 'external_id'})
                    _fields(provider, {'provider', 'external_id'} if minimal else fields)
                    _need(all(type(v) is str and bool(v.strip()) for v in provider.values()))
            elif field == 'user_id':
                _need(value is None, 'RECIPIENT_IDENTITY_UNSUPPORTED_IN_V2')
            elif field == 'user_is_platform_admin':
                _need(type(value) is int and value == 0, 'PRIVILEGE_POLICY_UNSUPPORTED')
            elif field == 'sync_enabled':
                _need(type(value) is int and value == 0, 'SYNC_ENABLEMENT_UNSUPPORTED')
            elif field in {'enabled', 'correspondent_enabled', 'club_is_active'}:
                _need(type(value) is int and value in (0, 1))
            elif field in {'id', 'season_id', 'week_id', 'number', 'round'}:
                _need(type(value) is int and value > 0)
            elif field in {'start_year', 'api_season_year', 'external_match_id'}:
                _need(value is None or (type(value) is int and value > 0))
            elif field == 'api_competition_code':
                _need(value is None or (type(value) is str and bool(value.strip())))
            elif field == 'key' or field.endswith('_key'):
                _need(_hash(value))
            else:
                _need(type(value) is str and bool(value.strip()))
        if kind == 'policy':
            _need(row['slug'] == row['slug'].strip().lower())
            _need(row['status'] in {'setup', 'active', 'archived'})
            try:
                instant = datetime.fromisoformat(row['timestamp'])
                _need(instant.tzinfo is not None, 'TIMESTAMP_TIMEZONE_REQUIRED')
            except ValueError:
                raise ManifestError('INVALID_TIMESTAMP') from None
        if kind == 'recipient':
            _need(row['recipient_kind'] in {'recap', 'admin_alert'})
            email = row['email_normalized']
            _need(email == email.strip().lower() and email.count('@') == 1 and
                  all(email.split('@')) and not any(c.isspace() for c in email), 'INVALID_RECIPIENT')
    if counts.get('policy') != 1: blockers.add('REVIEWED_POLICY_REQUIRED')
    if not counts.get('recipient'): blockers.add('RECIPIENT_PERMISSION_MANIFEST_REQUIRED')
    _need(_hash(document['checksum']) and document['checksum'] == manifest_checksum(document),
          'MANIFEST_CHECKSUM_MISMATCH')
    return {'schema_version': 2, 'status': 'blocked' if blockers else 'valid',
            'scope': 'structure_and_internal_consistency_only', 'execution_authorized': False,
            'checksum': document['checksum'], 'mapping_counts': counts,
            'blockers': [{'label': 'Unresolved', 'code': c} for c in sorted(blockers)]}
