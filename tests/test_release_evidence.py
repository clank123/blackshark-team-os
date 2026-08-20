"""History-independent self-check for the public v0.2 acceptance evidence."""

import csv
import hashlib
import json
import re
import unittest
from datetime import datetime
from pathlib import Path, PurePosixPath


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = Path("05_reviews/v0.2-隔离模拟证据.json")
FIXTURE_ROOT = Path("05_reviews/fixtures/v0.2-isolated-simulation")
TEN_FIELDS = [
    "动作",
    "执行线",
    "状态",
    "归属",
    "交付物/验收",
    "实际情况记录",
    "截止日期",
    "开始日期",
    "承接人",
    "阻塞/待确认",
]
SIX_STATUSES = ["未开始", "进行中", "已完成", "阻塞", "待拍板", "取消"]
FIVE_VIEWS = ["全部推进", "本月排期", "按承接人", "待拍板与阻塞", "门店要做"]
SEVEN_SECTIONS = [
    "# 1. 本月要做到什么",
    "# 2. 谁负责什么",
    "# 3. 本月排期",
    "# 4. 各板块怎么落地",
    "# 5. 本月先不做什么",
    "# 6. 还有哪些要拍板",
    "# 7. 这套文件怎么用",
]
EXPECTED_SCENARIO_CHECKS = {
    "scenario-01": {
        "zero_write_before_confirmation",
        "local_public_payload_substitute",
        "manifest_identity_valid",
        "critical_payload_valid",
        "atomic_activation",
        "local_map_created",
        "personal_directories_created",
        "network_download_verified",
    },
    "scenario-02": {
        "zero_write_before_confirmation",
        "obsidian_marker_detected",
        "workspace_embedded_in_child_directory",
        "legacy_note_hash_unchanged",
        "protected_legacy_inventory_hash_unchanged",
        "local_map_created",
    },
    "scenario-03": {
        "zero_obsidian_candidates",
        "independent_workspace_created",
        "manifest_locatable",
        "shared_context_locatable",
        "seven_store_index_locatable",
        "router_skill_locatable",
        "monthly_compiler_skill_locatable",
        "personal_directories_locatable",
        "local_map_locatable",
    },
    "scenario-04": {
        "five_artifacts_generated",
        "seven_sections_valid",
        "csv_ten_fields_valid",
        "json_ten_fields_valid",
        "six_statuses_valid",
        "five_views_valid",
        "action_key_unique",
        "no_template_placeholders",
        "local_relative_links_resolve",
        "feishu_external_write",
    },
    "scenario-05": {
        "incoming_manifest_valid",
        "official_baseline_difference_detected",
        "update_paused_before_switch",
        "official_local_edit_preserved",
        "personal_project_hash_unchanged",
        "local_map_hash_unchanged",
        "git_pull_used",
    },
}
FALSE_BOUNDARY_CHECKS = {
    "scenario-01": "network_download_verified",
    "scenario-04": "feishu_external_write",
    "scenario-05": "git_pull_used",
}


class EvidenceError(AssertionError):
    """Raised when public release evidence is not self-consistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    _require(bool(value), "empty relative path")
    _require(not pure.is_absolute(), "absolute path is forbidden")
    _require(".." not in pure.parts, "parent traversal is forbidden")
    _require("\\" not in value, "path must use POSIX separators")
    return Path(*pure.parts)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aggregate(entries: list[dict]) -> str:
    paths = [entry["relative_path"] for entry in entries]
    _require(paths == sorted(paths), "inventory paths are not sorted")
    _require(len(paths) == len(set(paths)), "inventory paths are not unique")
    rows = []
    for entry in entries:
        _require(
            set(entry) == {"relative_path", "byte_size", "file_sha256"},
            "inventory entry fields differ from contract",
        )
        _safe_relative(entry["relative_path"])
        _require(type(entry["byte_size"]) is int, "byte size must be an integer")
        _require(entry["byte_size"] >= 0, "byte size must be non-negative")
        _require(
            re.fullmatch(r"[0-9a-f]{64}", entry["file_sha256"]) is not None,
            "file hash is invalid",
        )
        rows.append(
            f"{entry['relative_path']}\t{entry['byte_size']}\t"
            f"{entry['file_sha256']}\n"
        )
    return hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def _validate_inventory(inventory: dict) -> None:
    _require(
        set(inventory) == {"entries", "aggregate_sha256"},
        "inventory fields differ from contract",
    )
    _require(
        inventory["aggregate_sha256"] == _aggregate(inventory["entries"]),
        "inventory aggregate does not match entries",
    )


def _validate_public_inventory(package_root: Path, inventory: dict) -> None:
    _require(
        inventory["inventory_scope_alias"] == "package-root",
        "public artifact scope must be package-root",
    )
    _validate_inventory(
        {
            "entries": inventory["entries"],
            "aggregate_sha256": inventory["aggregate_sha256"],
        }
    )
    for entry in inventory["entries"]:
        path = package_root / _safe_relative(entry["relative_path"])
        _require(path.is_file(), f"public artifact is missing: {entry['relative_path']}")
        _require(path.stat().st_size == entry["byte_size"], "artifact byte size differs")
        _require(_sha256(path) == entry["file_sha256"], "artifact hash differs")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _require(parsed.tzinfo is not None, "timestamp lacks timezone")
    _require(parsed.utcoffset() is not None, "timestamp lacks UTC offset")
    return parsed


def _validate_timeline(scenario: dict, scenario_id: str) -> None:
    snapshots = scenario["pre_confirmation_snapshots"]
    _require(len(snapshots) == 2, f"{scenario_id} needs two pre-confirm snapshots")
    for snapshot in snapshots:
        _validate_inventory(
            {
                "entries": snapshot["entries"],
                "aggregate_sha256": snapshot["aggregate_sha256"],
            }
        )
    _require(snapshots[0]["entries"] == snapshots[1]["entries"], "snapshots differ")
    _require(
        snapshots[0]["aggregate_sha256"] == snapshots[1]["aggregate_sha256"],
        "snapshot aggregates differ",
    )
    _require(
        snapshots[0]["entries"] == scenario["before_inventory"]["entries"],
        "snapshot does not cover the complete before scope",
    )
    events = scenario["event_log"]
    expected = [
        "detect_recommend",
        "simulated_confirmation",
        "unique_staging_created",
        "payload_copied",
        "manifest_validated",
        "atomic_rename",
        "post_activation",
    ]
    _require([event["event"] for event in events] == expected, "event order differs")
    _require([event["seq"] for event in events] == list(range(1, 8)), "seq differs")
    event_times = [_parse_time(event["at"]) for event in events]
    _require(event_times == sorted(event_times), "event times decrease")
    confirmation = event_times[1]
    first_write = event_times[2]
    snapshot_times = [_parse_time(snapshot["captured_at"]) for snapshot in snapshots]
    _require(all(value < confirmation for value in snapshot_times), "snapshot is not pre-confirm")
    _require(confirmation < first_write, "confirmation is not before first write")
    _require(event_times[4] < event_times[5], "manifest validation must precede rename")
    _require(event_times[-1] == max(event_times), "post activation must be last")
    _require(
        scenario["activation_state"]
        == {
            "staging_exists_after_activation": False,
            "final_target_exists_after_activation": True,
        },
        "activation state differs",
    )
    staging = scenario["staging_inventory"]
    _require(staging["entries"], "staging inventory is empty")
    _validate_inventory(
        {
            "entries": staging["entries"],
            "aggregate_sha256": staging["aggregate_sha256"],
        }
    )


def _validate_scenario_four(package_root: Path) -> None:
    outputs = package_root / FIXTURE_ROOT / "scenario-04" / "outputs"
    files = {
        "plan": outputs / "本月运营方案.md",
        "csv": outputs / "运营推进表.csv",
        "schema": outputs / "运营推进表结构.json",
        "release": outputs / "发布说明.md",
        "acceptance": outputs / "验收结果.md",
    }
    _require(all(path.is_file() for path in files.values()), "scenario 4 output is missing")
    plan = files["plan"].read_text(encoding="utf-8")
    positions = [plan.find(heading) for heading in SEVEN_SECTIONS]
    _require(all(position >= 0 for position in positions), "plan is not seven-section")
    _require(positions == sorted(positions), "seven sections are not ordered")
    _require(
        all(plan.count(heading) == 1 for heading in SEVEN_SECTIONS),
        "seven sections are not unique",
    )
    with files["csv"].open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    _require(reader.fieldnames == TEN_FIELDS, "CSV does not have the ten fields")
    actions = [row["动作"].strip() for row in rows]
    _require(all(actions), "CSV has an empty action")
    _require(len(actions) == len(set(actions)), "CSV actions are not unique")
    _require(all(row["状态"] in SIX_STATUSES for row in rows), "CSV status is invalid")
    schema = json.loads(files["schema"].read_text(encoding="utf-8"))
    _require([field["name"] for field in schema["fields"]] == TEN_FIELDS, "JSON fields differ")
    status_field = next(field for field in schema["fields"] if field["name"] == "状态")
    _require(status_field["options"] == SIX_STATUSES, "JSON statuses differ")
    _require([view["name"] for view in schema["views"]] == FIVE_VIEWS, "JSON views differ")
    for path in files.values():
        text = path.read_text(encoding="utf-8")
        _require("{{" not in text and "}}" not in text, "template placeholder remains")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", plan)
    _require(bool(links), "local relative links are missing")
    for target in links:
        relative = _safe_relative(target.removeprefix("./"))
        _require((files["plan"].parent / relative).is_file(), "local link is unresolved")
    release = files["release"].read_text(encoding="utf-8")
    acceptance = files["acceptance"].read_text(encoding="utf-8")
    for text in [release, acceptance]:
        _require("外部写入：无" in text, "external-write state is not no-write")
        _require("未请求" in text, "publication state is not unrequested")


def _validate_scenario_five(package_root: Path, scenario: dict) -> None:
    root = package_root / FIXTURE_ROOT / "scenario-05"
    incoming = root / "incoming"
    manifest = json.loads((incoming / "workspace-manifest.json").read_text(encoding="utf-8"))
    _require(manifest["workspace"] == "blackshark-team-os", "incoming workspace differs")
    _require(manifest["schema_version"] == 2, "incoming schema differs")
    _require(manifest["release"] == "v0.2", "incoming release differs")
    for relative in [
        "01_shared_context/黑鲨经营边界.md",
        "02_store_cards/00-七店索引.md",
        "04_templates/月度运营方案模板.md",
        ".agents/skills/blackshark-ops-router/SKILL.md",
        ".agents/skills/blackshark-monthly-ops-compiler/SKILL.md",
    ]:
        _require((incoming / relative).is_file(), f"incoming key payload missing: {relative}")
    current_baseline = root / "current" / "01_shared_context" / "黑鲨经营边界.md"
    incoming_baseline = incoming / "01_shared_context" / "黑鲨经营边界.md"
    _require(current_baseline.read_bytes() != incoming_baseline.read_bytes(), "baseline has no conflict")
    for protected in scenario["protected_artifacts"]:
        path = package_root / _safe_relative(protected["fixture_path"])
        digest = _sha256(path)
        _require(digest == protected["before_sha256"], "protected before hash differs")
        _require(digest == protected["after_sha256"], "protected after hash differs")


def validate_package(package_root: Path = PACKAGE_ROOT) -> None:
    package_root = package_root.resolve()
    evidence = json.loads((package_root / EVIDENCE_PATH).read_text(encoding="utf-8"))
    _require("source_commit" not in evidence, "evidence depends on repository history")
    _require(evidence["release"] == "v0.2", "release differs")
    source_payload = evidence["source_payload"]
    _require(source_payload["identity"] == "blackshark-team-os/v0.2", "source identity differs")
    for entry in source_payload["files"]:
        path = package_root / _safe_relative(entry["path"])
        _require(path.is_file(), "source payload file is missing")
        _require(path.stat().st_size == entry["byte_size"], "source payload size differs")
        _require(_sha256(path) == entry["file_sha256"], "source payload hash differs")
    input_fixture = evidence["input_fixture"]
    _require(input_fixture["encoding"] == "UTF-8", "input encoding differs")
    input_path = package_root / _safe_relative(input_fixture["path"])
    _require(input_path.is_file(), "input fixture is missing")
    input_hash = _sha256(input_path)
    _require(input_hash == input_fixture["file_sha256"], "input fixture hash differs")
    _require(input_hash == evidence["input_sha256"], "input evidence hash differs")

    scenarios = evidence["scenarios"]
    _require(set(scenarios) == {f"scenario-0{index}" for index in range(1, 6)}, "scenario set differs")
    for scenario_id, scenario in scenarios.items():
        _require(scenario["status"] == "通过", f"{scenario_id} did not pass")
        checks = scenario["checks"]
        expected_checks = EXPECTED_SCENARIO_CHECKS[scenario_id]
        _require(
            set(checks) == expected_checks,
            f"{scenario_id} check keys differ from contract",
        )
        false_boundary = FALSE_BOUNDARY_CHECKS.get(scenario_id)
        for check_name in expected_checks:
            value = checks[check_name]
            _require(
                type(value) is bool,
                f"{scenario_id} check is not boolean: {check_name}",
            )
            if check_name == false_boundary:
                _require(
                    value is False,
                    f"{scenario_id} boundary check must be false: {check_name}",
                )
            else:
                _require(
                    value is True,
                    f"{scenario_id} expected check did not pass: {check_name}",
                )
        _safe_relative(scenario["inventory_scope_alias"])
        _validate_inventory(scenario["before_inventory"])
        _validate_inventory(scenario["after_inventory"])
        artifact_inventory = scenario["artifact_inventory"]
        _validate_public_inventory(package_root, artifact_inventory)
        projection = {
            entry["relative_path"]: entry["file_sha256"]
            for entry in artifact_inventory["entries"]
        }
        _require(projection == scenario["artifact_sha256"], "artifact projection differs")
    _validate_timeline(scenarios["scenario-01"], "scenario-01")
    _validate_timeline(scenarios["scenario-02"], "scenario-02")
    _validate_scenario_four(package_root)
    _validate_scenario_five(package_root, scenarios["scenario-05"])
    _require(
        evidence["limitations"]
        == [
            "未验证网络下载",
            "未验证真实 Obsidian GUI",
            "未验证真实飞书",
            "未完成运营人员 A/B 实机独立验收",
        ],
        "limitations differ",
    )


class PublicReleaseEvidenceTests(unittest.TestCase):
    def test_public_evidence_is_self_contained(self) -> None:
        validate_package(PACKAGE_ROOT)


if __name__ == "__main__":
    unittest.main()
