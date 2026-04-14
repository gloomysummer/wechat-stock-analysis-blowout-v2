#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成结构化风险底稿，供公众号文章写作阶段使用。

用法：
  python3 scripts/generate_risk_brief.py <financial_data.json> <risk_brief.md> [company_name]
"""

import json
import math
import sys
from datetime import datetime
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def safe_float(value):
    try:
        if value is None:
            return None
        result = float(value)
        return None if result != result else result
    except Exception:
        return None


def latest_row(rows):
    if not isinstance(rows, list) or not rows:
        return {}
    def key_fn(row):
        return (str(row.get('end_date') or ''), str(row.get('ann_date') or ''))
    return sorted(rows, key=key_fn, reverse=True)[0]


def fmt_yi(value):
    value = safe_float(value)
    if value is None:
        return '无'
    return f"{value / 1e8:.2f}亿"


def fmt_pct(value):
    value = safe_float(value)
    if value is None:
        return '无'
    return f"{value:.2f}%"


def risk_level(score, high=80, medium=60):
    if score is None:
        return '待核'
    if score >= high:
        return '高'
    if score >= medium:
        return '中'
    return '低'


def audit_risk(audit_row):
    result = str(audit_row.get('audit_result') or '').strip()
    if not result:
        return None
    if '无法表示' in result or '否定' in result:
        return {
            'theme': '审计与持续经营风险',
            'severity': '高',
            'fact': f'最近审计意见为“{result}”。',
            'direct': '审计层面对财务真实性或持续经营能力提出重大疑问。',
            'mid': '通常对应资产减值、债务压力、持续经营不确定性或内部控制缺陷。',
            'deep': '需继续核查更早期债务、资产质量、经营现金流和监管事项是否共同放大风险。',
            'status': '当前仍应视为高敏感风险变量。',
            'evidence': 'A',
        }
    if ('保留' in result and '无保留' not in result) or '持续经营' in result:
        return {
            'theme': '审计边界风险',
            'severity': '中高',
            'fact': f'最近审计意见为“{result}”。',
            'direct': '审计机构已经对部分事项保留意见或提示持续经营边界。',
            'mid': '通常意味着财务口径、资产确认、经营稳定性或债务安排存在待解释空间。',
            'deep': '需继续追问触发保留意见的根因，是经营恶化、债务问题，还是资产处置与会计判断。',
            'status': '在审计结论恢复标准前，仍属于持续风险项。',
            'evidence': 'A',
        }
    return None


def liquidity_risk(fi_row):
    current_ratio = safe_float(fi_row.get('current_ratio'))
    quick_ratio = safe_float(fi_row.get('quick_ratio'))
    debt_ratio = safe_float(fi_row.get('debt_to_assets'))
    ocf_to_profit = safe_float(fi_row.get('ocf_to_profit'))

    if all(v is None for v in [current_ratio, quick_ratio, debt_ratio, ocf_to_profit]):
        return None

    pressure = 0
    facts = []
    if current_ratio is not None:
        facts.append(f"流动比率 {current_ratio:.2f}")
        if current_ratio < 1:
            pressure += 1
    if quick_ratio is not None:
        facts.append(f"速动比率 {quick_ratio:.2f}")
        if quick_ratio < 0.7:
            pressure += 1
    if debt_ratio is not None:
        facts.append(f"资产负债率 {debt_ratio:.2f}%")
        if debt_ratio > 70:
            pressure += 1
    if ocf_to_profit is not None:
        facts.append(f"经营现金流/利润比 {ocf_to_profit:.2f}%")
        if ocf_to_profit < 60:
            pressure += 1

    if pressure == 0:
        return None

    severity = '高' if pressure >= 3 else '中'
    return {
        'theme': '流动性与债务边界风险',
        'severity': severity,
        'fact': '，'.join(facts) + '。',
        'direct': '当前财务结构已经出现流动性、杠杆或现金流覆盖能力上的边界信号。',
        'mid': '中层原因通常落在债务结构、营运资金占用、应收与存货、资本开支或扩张节奏上。',
        'deep': '需继续核查是否存在借新还旧、担保链条、评级压力、商票风险或更早形成的债务积累机制。',
        'status': '只要这些指标未明显修复，就仍是当前有效风险变量。',
        'evidence': 'A',
    }


def performance_risk(fi_row, latest_disclosure):
    netprofit_yoy = safe_float(fi_row.get('netprofit_yoy'))
    revenue_yoy = safe_float(fi_row.get('tr_yoy') or fi_row.get('or_yoy'))
    source_title = str((latest_disclosure or {}).get('source_title') or '').strip()
    source_type = str((latest_disclosure or {}).get('source_type') or '').strip()
    profit_info = ((latest_disclosure or {}).get('metrics') or {}).get('net_profit') or {}

    negative_estimate = False
    if source_type and 'forecast' in source_type:
        min_v = safe_float(profit_info.get('min'))
        max_v = safe_float(profit_info.get('max'))
        if min_v is not None and max_v is not None and max_v < 0:
            negative_estimate = True

    if netprofit_yoy is None and revenue_yoy is None and not negative_estimate:
        return None

    trigger = []
    score = 0
    if revenue_yoy is not None:
        trigger.append(f"营收同比 {revenue_yoy:+.2f}%")
        if revenue_yoy < -10:
            score += 1
    if netprofit_yoy is not None:
        trigger.append(f"净利润同比 {netprofit_yoy:+.2f}%")
        if netprofit_yoy < -20:
            score += 1
    if negative_estimate:
        trigger.append(f"{source_title or '最新业绩预告'}为亏损区间")
        score += 2

    if score == 0:
        return None

    severity = '高' if score >= 2 else '中'
    return {
        'theme': '业绩恶化或盈利兑现风险',
        'severity': severity,
        'fact': '，'.join(trigger) + '。',
        'direct': '当前披露已经显示收入、利润或盈利兑现能力出现显著压力。',
        'mid': '中层原因通常在需求回落、价格压力、成本传导失效、资产减值或扩张后遗症。',
        'deep': '需继续结合订单、客户、行业景气与债务安排看，这只是阶段波动，还是更深层经营拐点。',
        'status': '若最新披露仍未扭转，这一风险仍处于进行中。',
        'evidence': 'A',
    }


def shareholder_risk(holder_rows):
    if not isinstance(holder_rows, list) or not holder_rows:
        return None
    recent = sorted(holder_rows, key=lambda r: str(r.get('ann_date') or ''), reverse=True)[:8]
    de_rows = [r for r in recent if str(r.get('in_de') or '').upper() == 'DE']
    if not de_rows:
        return None

    names = []
    for row in de_rows[:4]:
        name = str(row.get('holder_name') or '').strip()
        if name and name not in names:
            names.append(name)
    trigger = f"近期股东/高管减持主体包括：{'、'.join(names[:4])}。" if names else '近期存在减持记录。'
    return {
        'theme': '股东与控制权观察风险',
        'severity': '中',
        'fact': trigger,
        'direct': '当前至少能确认股东或高管层面的减持方向性信号。',
        'mid': '中层原因需要继续区分：是正常流动性安排、股权激励解禁，还是控制权、资金链、市场预期压力。',
        'deep': '需外部继续核查是否伴随高比例质押、冻结、控制权变化、纾困或资产出售。',
        'status': '如果减持只是零散动作，风险级别有限；若叠加质押或控制权变化，应升级关注。',
        'evidence': 'A',
    }


def disclosure_risk(disclosure):
    if not disclosure:
        return None
    source_type = str(disclosure.get('source_type') or '')
    source_title = str(disclosure.get('source_title') or '最新披露')
    if 'forecast' in source_type:
        return {
            'theme': '预告口径与实际兑现风险',
            'severity': '中',
            'fact': f'最新披露来自 {source_title}，属于预告或预测口径。',
            'direct': '预告口径本身就意味着结果仍有区间和兑现偏差。',
            'mid': '中层原因通常在盈利波动、资产处置、减值确认、非经常性项目或需求变化。',
            'deep': '需结合后续正式年报、季报和市场反应判断，这只是过渡口径，还是风险进一步暴露的前奏。',
            'status': '在正式报告落地前，这一风险持续有效。',
            'evidence': 'A',
        }
    return None


def detect_market(stock_basic):
    ts_code = str((stock_basic or {}).get('ts_code') or '').upper()
    if ts_code.endswith('.HK'):
        return 'HK'
    return 'CN'


def build_query_lines(company, market):
    common = [
        f'{company} 质押 冻结 控制权变更',
        f'{company} 立案调查 行政处罚 问询函',
        f'{company} 诉讼 被执行 失信 资产冻结',
        f'{company} 债务违约 债务展期 评级下调',
        f'{company} 商票 承兑 逾期',
        f'{company} 审计意见 持续经营重大不确定性',
    ]
    if market == 'HK':
        common.extend([
            f'{company} 退市 私有化 协议安排 联合公告',
            f'{company} delisting privatization scheme of arrangement',
            f'{company} 中国信达 华融 AMC 纾困 债务重组',
        ])
    return common


def build_timeline(data):
    latest_disclosure = data.get('latest_disclosure') or {}
    stock_basic = latest_row(data.get('stock_basic') or [])
    audit = latest_row(data.get('fina_audit') or [])
    holder_rows = sorted((data.get('holdertrade') or []), key=lambda r: str(r.get('ann_date') or ''), reverse=True)[:5]

    lines = []
    if stock_basic:
        lines.append(f"- 上市基础：{stock_basic.get('name', '公司')}（{stock_basic.get('ts_code', '')}），上市日期 {stock_basic.get('list_date', '未知')}。")
    if latest_disclosure:
        lines.append(
            f"- 最新披露：{latest_disclosure.get('source_title', '未知')}，报告期 {latest_disclosure.get('report_period', '未知')}，披露日 {latest_disclosure.get('disclosure_date', '未知')}。"
        )
    if audit:
        lines.append(
            f"- 最近审计：{audit.get('end_date', '未知')} 审计意见为 {audit.get('audit_result', '未知')}。"
        )
    if holder_rows:
        holder_lines = []
        for row in holder_rows[:3]:
            name = str(row.get('holder_name') or '').strip()
            act = '增持' if str(row.get('in_de') or '').upper() == 'IN' else '减持'
            holder_lines.append(f"{row.get('ann_date', '')} {name} {act}")
        if holder_lines:
            lines.append(f"- 近期股东变动：{'；'.join(holder_lines)}。")
    return lines


def main():
    if len(sys.argv) < 3:
        raise SystemExit('usage: generate_risk_brief.py <financial_data.json> <risk_brief.md> [company_name]')

    data_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    company_arg = sys.argv[3] if len(sys.argv) > 3 else ''
    data = load_json(data_path)

    stock_basic = latest_row(data.get('stock_basic') or [])
    fi_row = latest_row(data.get('fina_indicator') or [])
    audit_row = latest_row(data.get('fina_audit') or [])
    latest_disclosure = data.get('latest_disclosure') or {}
    holder_rows = data.get('holdertrade') or []
    company = company_arg or stock_basic.get('name') or '目标公司'
    market = detect_market(stock_basic)

    cards = []
    for item in [
        audit_risk(audit_row),
        liquidity_risk(fi_row),
        performance_risk(fi_row, latest_disclosure),
        shareholder_risk(holder_rows),
        disclosure_risk(latest_disclosure),
    ]:
        if item:
            cards.append(item)

    # 去重 theme
    seen = set()
    deduped = []
    for card in cards:
        if card['theme'] in seen:
            continue
        seen.add(card['theme'])
        deduped.append(card)
    cards = deduped[:5]

    lines = [
        f'# 风险底稿：{company}',
        '',
        f'- 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        f'- 数据来源：{data_path.name}',
        f'- 市场识别：{"港股/港股历史链" if market == "HK" else "A股/内地公开披露链"}',
        '',
        '## 一、当前已识别的强风险触发器',
        '',
    ]

    if cards:
        for card in cards:
            lines.append(f"- [{card['severity']}风险][{card['evidence']}级证据] {card['theme']}：{card['fact']}")
    else:
        lines.append('- 当前财务底稿中未识别到强触发器，但这不代表没有外部监管、诉讼、控制权或舆情风险，仍需外部检索补充。')

    lines.extend(['', '## 二、结果事实时间轴', ''])
    timeline = build_timeline(data)
    if timeline:
        lines.extend(timeline)
    else:
        lines.append('- 当前底稿缺少足够的时间轴信息，需要结合公告继续补齐。')

    lines.extend(['', '## 三、结构化风险卡片', ''])
    if cards:
        for idx, card in enumerate(cards, 1):
            lines.extend([
                f"### {idx}. {card['theme']}",
                '',
                f"- 结果事实：{card['fact']}",
                f"- 直接原因线索：{card['direct']}",
                f"- 中层原因候选：{card['mid']}",
                f"- 更深层机制待检：{card['deep']}",
                f"- 当前判断：{card['status']}",
                f"- 证据等级：{card['evidence']}级",
                '',
            ])
    else:
        lines.append('- 当前底稿暂未自动生成风险卡片，建议优先从控制权、监管、诉讼、信用、审计五条线补查。')
        lines.append('')

    lines.extend(['## 四、需要外部继续补查的问题', ''])
    external_questions = [
        '最近5年内是否出现过立案调查、行政处罚、交易所问询或持续监管关注？',
        '控股股东是否存在高比例质押、冻结、被动减持或控制权变化？',
        '是否存在重大诉讼、被执行、失信、资产冻结或清盘相关信息？',
        '是否存在商票逾期、债务展期、评级下调、借新还旧等信用边界信号？',
        '如果公司曾经经历退市、私有化、纾困或重大重组，这条链条的起点和今天的影响是什么？',
    ]
    lines.extend([f'- {q}' for q in external_questions])

    lines.extend(['', '## 五、建议检索词', ''])
    lines.extend([f'- {q}' for q in build_query_lines(company, market)])

    lines.extend(['', '## 六、写作提醒', ''])
    lines.extend([
        '- 风险段优先把重大风险写成一条因果链，而不是并列标签。',
        '- 如果只看到结果事实，还要继续追问直接原因、中层原因和更深层机制。',
        '- 如果更深层机制只能从市场讨论中看到，必须保留边界，不能写成公司已确认结论。',
        '- 近5年默认看现状；如果重大风险链条明显更早，就继续往前追到因果链闭合。',
    ])

    out_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(str(out_path))


if __name__ == '__main__':
    main()
