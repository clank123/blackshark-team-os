# 保存与接续

沿原项目保存，不要求人为维护额外报表。最少保留：该店资料入口与来源清单、当前人群库、月度判断/原方案指针、实际结果、库修订历史。每次接续同时读取基线库与其修订记录。

已有可靠的版本/反馈存储就沿用。没有时可用本包 `scripts/planning_state.py` 维护一个本地JSON：保存来源获取状态、主张最新修订及完整历史、规划指针和实际结果引用。它不取平台数据、不调度、不发送、不写飞书，也不替代共享数据库。锁与原子保存针对同一文件系统；团队跨设备应指定一个写入方，或由既有共享服务处理并发。锁残留时先确认上次进程已结束、文件可回读，再由维护者清理，不能自动抢占。

所有文件路径由当前授权工作区或私有适配层提供。原始资料仍留原处；state中的引用相对约定工作区根或为授权来源URL。不可把包目录当业务存储，升级Skill不碰私有state。

## 初次接入

在授权项目目录准备 context JSON：

```json
{"library_ref":"stores/example/audience.json","context_ref":"stores/example/context.md","source_register_ref":"stores/example/sources.json"}
```

```sh
python3 scripts/planning_state.py init --state /authorized/project/state.json --store example --context /authorized/project/context.json
python3 scripts/planning_state.py read --state /authorized/project/state.json
```

`/authorized/project` 是占位路径，使用真实已授权目录。脚本路径相对本Skill。第一次修订某个主张，AI须先回读基线并核对 `before`；脚本只能校验后续已登记主张，不能自动证明第一次文本与原文一致。

首次导入历史资料时，把已知覆盖和未知成功时间保留在 `source_register_ref` 指向的来源登记，不为初始化伪造一次成功采集。读取资料时同时看基线登记与state的新尝试；state尚无成功记录不代表历史资料不存在。真实新一轮成功后才用source_refresh推进覆盖。

## 记录一轮变化

事件共同字段：`id`（同次重试复用）、`store_id`、`kind`、`occurred_at`（ISO日期时间）、`actor`、`summary`、`references`（非空、可回读来源）。按类型增加：

| kind | 必填字段 |
| --- | --- |
| source_refresh | source_id、status(success/partial/failed)、attempted_at；成功另需coverage/source_version/checks；失败或部分成功需limitation，部分取得范围用partial_coverage，不推进完整成功位置 |
| card_revision | card_id、claim_id、before、after、reason、claim_status、basis、impact、unknowns |
| planning_note | 共同字段足够；summary说明决定/建议身份，references指本轮方案与原问题 |
| result_observation | action_id、execution、window、customer_result、business_result、confounders、next_observation；未知直接写未知 |

卡片 `claim_status` 为 candidate/supported_in_scope/limited/withdrawn/unresolved/stale。`basis` 为 source_correction/new_expression/action_result/scope_change。动作结果型修订还需 `result_checks`：execution、exposure、fulfillment、window、confounders、conclusion_boundary；其中 execution=occurred 且 window=mature 才允许标 supported_in_scope，但这只是最低结构检查，不能代替证据判断。

```sh
python3 scripts/planning_state.py record --state /authorized/project/state.json --expected-revision 0 --event /authorized/project/event.json
```

读取最新revision再写入。版本冲突重新读取并合并；同ID同内容重试不重复，不同内容会拒绝。保存后回读；答复分别说明“建议形成”“本地保存”“共享已同步”，没有实际回执不声称共享成功。

结构检查不判断来源真假、经营因果或权限。不要把原始订单或完整评论塞入日志。若改用现有数据库，保留同样的来源、版本、冲突和重试语义，无需重复维护两套库。
