#!/usr/bin/env python3
"""Small local planning journal. No network, scheduler, or business-system writes."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(obj, keys):
    for key in keys:
        require(isinstance(obj.get(key), str) and obj[key].strip(), f"缺少文字字段：{key}")


def validate_event(event):
    nonempty(event, ["id", "store_id", "kind", "occurred_at", "actor", "summary"])
    datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    require(isinstance(event.get("references"), list) and event["references"], "需要可回读来源 references")
    require(all(isinstance(x, str) and x.strip() for x in event["references"]), "来源引用须是非空字符串")
    kind = event["kind"]
    require(kind in {"source_refresh", "card_revision", "planning_note", "result_observation"}, "未知记录类型")
    if kind == "source_refresh":
        nonempty(event, ["source_id", "status", "attempted_at"])
        datetime.fromisoformat(event["attempted_at"].replace("Z", "+00:00"))
        require(event["status"] in {"success", "partial", "failed"}, "来源状态无效")
        if event["status"] == "success":
            nonempty(event, ["coverage", "source_version", "checks"])
        else:
            nonempty(event, ["limitation"])
            require("coverage" not in event, "部分成功/失败不能推进完整覆盖；实际取得范围写 partial_coverage")
    if kind == "card_revision":
        nonempty(event, ["card_id", "claim_id", "before", "after", "reason", "claim_status", "basis", "impact", "unknowns"])
        require(event["claim_status"] in {"candidate", "supported_in_scope", "limited", "withdrawn", "unresolved", "stale"}, "主张状态无效")
        require(event["basis"] in {"source_correction", "new_expression", "action_result", "scope_change"}, "修订依据无效")
        if event["basis"] == "action_result":
            checks = event.get("result_checks", {})
            nonempty(checks, ["execution", "exposure", "fulfillment", "window", "confounders", "conclusion_boundary"])
            if event["claim_status"] == "supported_in_scope":
                require(checks["execution"] == "occurred" and checks["window"] == "mature", "未执行或窗口未成熟不能按动作结果提升主张")
    if kind == "result_observation":
        nonempty(event, ["action_id", "execution", "window", "customer_result", "business_result", "confounders", "next_observation"])


@contextmanager
def locked(path):
    require(not path.is_symlink(), "状态文件不能是软链接")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("另一次更新进行中或上次中断；先核实际写入进程和当前版本，勿强行覆盖")
    try:
        yield
    finally:
        lock.rmdir()


def save(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def initialize(path, store_id, context):
    nonempty(context, ["library_ref", "context_ref"])
    with locked(path):
        require(not path.exists(), "已存在当前记录；不能重新初始化覆盖")
        state = {"schema_version": 1, "store_id": store_id, "revision": 0,
                 "context": context, "source_checks": {}, "claims": {}, "events": []}
        save(path, state)
    return {"saved": True, "revision": 0, "path": str(path)}


def record(path, expected_revision, event):
    validate_event(event)
    require(len(json.dumps(event, ensure_ascii=False).encode()) <= 65536, "这里只保存引用与摘要，不放原始明细")
    with locked(path):
        state = read_json(path)
        require(state["schema_version"] == 1, "不支持的记录版本")
        require(event["store_id"] == state["store_id"], "门店不匹配，不能混写")
        previous = next((x for x in state["events"] if x["event"]["id"] == event["id"]), None)
        if previous:
            require(previous["event"] == event, "同一事件ID的内容不同，需核对修订，不能静默覆盖")
            return {"saved": False, "duplicate": True, "revision": state["revision"]}
        require(state["revision"] == expected_revision, "基准版本已变化；重读当前库并合并后重试")
        if event["kind"] == "source_refresh":
            source = state["source_checks"].setdefault(event["source_id"], {})
            source.update({"last_attempt": event["attempted_at"], "last_status": event["status"], "last_event": event["id"]})
            if event["status"] == "success":
                source.update({"last_success": event["attempted_at"], "coverage": event["coverage"],
                               "source_version": event["source_version"], "successful_event": event["id"]})
        if event["kind"] == "card_revision":
            card = state["claims"].setdefault(event["card_id"], {})
            claim = card.get(event["claim_id"])
            if claim:
                require(claim["text"] == event["before"], "旧主张与当前记录不一致；重读后合并")
            card[event["claim_id"]] = {"text": event["after"], "status": event["claim_status"],
                                        "event_id": event["id"], "references": event["references"]}
        state["revision"] += 1
        state["events"].append({"revision": state["revision"], "recorded_at": datetime.now(timezone.utc).isoformat(), "event": event})
        save(path, state)
        require(read_json(path) == state, "保存后回读不一致")
    return {"saved": True, "revision": state["revision"], "path": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--state", type=Path, required=True)
    init.add_argument("--store", required=True)
    init.add_argument("--context", type=Path, required=True)
    read = sub.add_parser("read")
    read.add_argument("--state", type=Path, required=True)
    write = sub.add_parser("record")
    write.add_argument("--state", type=Path, required=True)
    write.add_argument("--expected-revision", type=int, required=True)
    write.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.state, args.store, read_json(args.context))
        elif args.command == "record":
            result = record(args.state, args.expected_revision, read_json(args.event))
        else:
            result = read_json(args.state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"未完成：{error}\n")


if __name__ == "__main__":
    main()
