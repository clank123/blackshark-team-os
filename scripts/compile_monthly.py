#!/usr/bin/env python3
"""Validate a monthly task tree and export local previews. No remote calls."""
import argparse
import csv
import json
import re
from datetime import date
from pathlib import Path

STATUSES = {'未开始', '进行中', '已完成', '阻塞', '待拍板', '取消'}
FIELDS = ['动作','执行线','状态','归属','交付物/验收','实际情况记录','截止日期','开始日期','承接人','阻塞/待确认','任务编号','父任务']
ROOT = Path(__file__).resolve().parents[1]


def validate(data, final=False):
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ['输入必须是对象'], []
    if not isinstance(data.get('store'), str) or not data['store'].strip():
        errors.append('门店为空')
    month = data.get('month', '')
    try:
        if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError()
        date.fromisoformat(month + '-01')
    except ValueError:
        errors.append('月份必须是有效 YYYY-MM')
    records = data.get('records')
    if not isinstance(records, list) or not records:
        return errors + ['records 必须是非空列表'], warnings
    by_id = {}
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            errors.append(f'第{index + 1}条不是对象'); continue
        rid = row.get('id')
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', rid):
            errors.append(f'第{index + 1}条编号无效'); continue
        if rid in by_id:
            errors.append(f'{rid}: 编号重复'); continue
        by_id[rid] = row
        for name in ['title', 'line', 'affiliation']:
            if not isinstance(row.get(name), str) or not row[name].strip():
                errors.append(f'{rid}: {name} 为空或非文本')
        if row.get('kind') not in ('project', 'task'):
            errors.append(f'{rid}: kind 必须是 project 或 task')
        if not isinstance(row.get('status'), str) or row['status'] not in STATUSES:
            errors.append(f'{rid}: 状态无效')
        for key in ['owner','acceptance','actual','blocker']:
            if row.get(key) is not None and not isinstance(row[key], str):
                errors.append(f'{rid}: {key} 必须是文本或空')
        parent = row.get('parent_id')
        if parent is not None and not isinstance(parent, str):
            errors.append(f'{rid}: 父编号必须是文本或 null')
        elif isinstance(parent, str) and not parent.strip():
            errors.append(f'{rid}: 根项目父编号须用 null，不能为空字符串')
        deps = row.get('dependencies', [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            errors.append(f'{rid}: dependencies 必须是编号列表')
        elif len(deps) != len(set(deps)):
            errors.append(f'{rid}: 前置重复')
        dates = {}
        for key in ['start','due']:
            value = row.get(key)
            if value in (None, ''): continue
            try:
                if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError()
                dates[key] = date.fromisoformat(value)
            except ValueError:
                errors.append(f'{rid}: {key} 日期无效')
        if len(dates) == 2 and dates['start'] > dates['due']:
            errors.append(f'{rid}: 开始晚于截止')
        def meaningful(value):
            return isinstance(value, str) and bool(value.strip())
        if row.get('status') in ('待拍板', '阻塞') and not meaningful(row.get('blocker')):
            errors.append(f'{rid}: {row["status"]} 需要注明卡点')
        if row.get('kind') == 'task' and row.get('status') != '取消':
            missing = [k for k in ['owner','start','due','acceptance'] if not meaningful(row.get(k))]
            if 'owner' not in missing and re.search(r'待定|待确认|待安排|待补|待分配|待指定|未知|未指定|未确认|\bTBD\b|\bTODO\b', row['owner'], re.IGNORECASE):
                missing.append('owner')
            # A concrete acceptance may legitimately ask to list unknowns.
            # Reject a placeholder value, not words inside useful criteria.
            if 'acceptance' not in missing and re.fullmatch(r'(?:验收[：:]?\s*)?(?:待定|待确认|待补|待补充|未知|暂无|TBD|TODO)[。.!！\s]*', row['acceptance'].strip(), re.IGNORECASE):
                missing.append('acceptance')
            if missing:
                message = f'{rid}: 缺少 ' + ', '.join(missing)
                (errors if final and row.get('status') != '待拍板' else warnings).append(message)
        if row.get('status') == '已完成' and not meaningful(row.get('actual')):
            (errors if final else warnings).append(f'{rid}: 完成状态没有实际记录或证据入口')
    if errors:
        return errors, warnings
    children = {rid: [] for rid in by_id}
    for rid, row in by_id.items():
        parent = row.get('parent_id')
        if parent:
            if parent not in by_id:
                errors.append(f'{rid}: 父项不存在')
            elif by_id[parent]['kind'] != 'project':
                errors.append(f'{rid}: 父项必须为项目')
            else:
                children[parent].append(rid)
        elif row['kind'] == 'task':
            errors.append(f'{rid}: 子任务缺少主项目')
        for dep in row.get('dependencies', []):
            if dep not in by_id:
                errors.append(f'{rid}: 前置不存在: {dep}')
    def cycle(graph, label):
        seen, stack = set(), set()
        def visit(node):
            if node in stack: return True
            if node in seen: return False
            stack.add(node)
            for nxt in graph[node]:
                if nxt in graph and visit(nxt): return True
            stack.remove(node); seen.add(node)
            return False
        if any(visit(n) for n in graph if n not in seen):
            errors.append(label + '存在循环')
    cycle({rid: [row['parent_id']] if row.get('parent_id') else [] for rid,row in by_id.items()}, '父子关系')
    cycle({rid: row.get('dependencies', []) for rid,row in by_id.items()}, '前置依赖')
    if errors: return errors, warnings
    for rid, row in by_id.items():
        if row['kind'] == 'project' and not children[rid]:
            warnings.append(f'{rid}: 项目尚无子任务')
        if row['status'] == '已完成' and any(by_id[c]['status'] not in ('已完成','取消') for c in children[rid]):
            errors.append(f'{rid}: 项目已完成但有未完成子项')
        for dep in row.get('dependencies', []):
            other = by_id[dep]
            if other['status'] == '取消' and row['status'] not in ('取消','待拍板','阻塞'):
                (errors if final else warnings).append(f'{rid}: 前置 {dep} 已取消，需要处理依赖')
            if row.get('start') and other.get('due') and row['start'] < other['due']:
                warnings.append(f'{rid}: 开始早于前置 {dep} 截止，核对实际交接')
        parent = row.get('parent_id')
        if parent:
            p = by_id[parent]
            if p['status'] == '取消' and row['status'] != '取消':
                errors.append(f'{rid}: 父项目取消但子项仍在推进')
    return errors, warnings


def spreadsheet_text(value):
    value = '' if value is None else str(value)
    # CSV can be opened in spreadsheet software. Prevent formula interpretation.
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def md(value):
    return str(value or '').replace('|', '\\|').replace('\n', ' / ')


def export(data, out, final=False):
    errors, warnings = validate(data, final)
    if errors: raise ValueError('\n'.join(errors))
    out = Path(out)
    # Never overwrite an earlier revision or follow an existing output symlink.
    if out.exists() or out.is_symlink():
        raise FileExistsError('输出目录已存在；选择新版本目录，保留原产物')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()
    rows = data['records']
    schema = json.loads((ROOT/'04_templates/运营推进表模板/schema.json').read_text())
    schema['store'],schema['month'] = data['store'],data['month']
    for field in schema['fields']:
        if field.get('dynamic_options'):
            field['options'] = list(dict.fromkeys(row['line'] for row in rows));field.pop('dynamic_options')
        if field.get('options_template'):
            field['options'] = list(dict.fromkeys(['黑鲨',data['store']]+[row['affiliation'] for row in rows]));field.pop('options_template')
    def resolve(value):
        if isinstance(value, str): return value.replace('{{目标门店}}', data['store'])
        if isinstance(value, list): return [resolve(v) for v in value]
        if isinstance(value, dict): return {k:resolve(v) for k,v in value.items()}
        return value
    schema = resolve(schema)
    with (out/'运营推进表.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer = csv.writer(f);writer.writerow(FIELDS)
        for row in rows:
            block = row.get('blocker') or ''
            if row.get('dependencies'): block += ('；' if block else '') + '前置：' + '、'.join(row['dependencies'])
            values = [row['title'],row['line'],row['status'],row['affiliation'],row.get('acceptance'),row.get('actual'),row.get('due'),row.get('start'),row.get('owner'),block,row['id'],row.get('parent_id')]
            writer.writerow([spreadsheet_text(v) for v in values])
    children = {}
    for row in rows: children.setdefault(row.get('parent_id'), []).append(row)
    lines = [f'# {md(data["store"])}｜{md(data["month"])} 推进表预览', '', '本地树形预览；飞书父子展开与文档内嵌尚未执行。', '']
    def render(parent=None, depth=0):
        for row in children.get(parent, []):
            lines.append('  '*depth + f'- **{md(row["title"])}** `{row["id"]}`｜{md(row["status"])}｜{md(row.get("owner")) or "承接人待定"}｜{md(row.get("due")) or "日期待定"}')
            lines.append('  '*(depth+1) + f'验收：{md(row.get("acceptance")) or "待补"}')
            if row.get('blocker'): lines.append('  '*(depth+1)+f'卡点：{md(row["blocker"])}')
            render(row['id'], depth+1)
    render()
    (out/'推进表预览.md').write_text('\n'.join(lines)+'\n')
    (out/'运营推进表结构.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2)+'\n')
    result = {'scope':'local_structure_only','final_tasks_checked':final,'records':len(rows),'warnings':warnings,'errors':[], 'external':{key:'未执行' for key in ['document','base_data','hierarchy','embedded_view']}}
    (out/'本地结构检查.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--final',action='store_true')
    a=p.parse_args()
    try:
        result=export(json.loads(a.input.read_text()),a.out,a.final)
    except (ValueError,OSError) as error:
        p.exit(1, str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
