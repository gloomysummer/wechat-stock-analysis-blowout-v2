#!/usr/bin/env python3
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def latest_row(rows):
    if not isinstance(rows, list) or not rows:
        return {}
    def key_fn(row):
        return (str(row.get('end_date') or ''), str(row.get('ann_date') or ''))
    return sorted(rows, key=key_fn, reverse=True)[0]


def fmt_yi(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return '无'
    return f"{value / 1e8:.2f}亿"


def fmt_pct(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return '无'
    return f"{value:.2f}%"


def safe_num(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def detect_metric(text, patterns):
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1)), m.group(0)
            except Exception:
                continue
    return None, ''


def detect_quarter_metric(text, metric_name):
    quarter_patterns = {
        'revenue': [
            r'(?:截至\d{4}年\d{1,2}月\d{1,2}日[^。\n]{0,80}?营收)[^\n。；;]{0,12}?(-?\d+(?:\.\d+)?)\s*亿',
            r'(?:前三季度[^。\n]{0,80}?营收)[^\n。；;]{0,12}?(-?\d+(?:\.\d+)?)\s*亿',
            r'(?:Q3[^。\n]{0,80}?营收)[^\n。；;]{0,12}?(-?\d+(?:\.\d+)?)\s*亿',
        ],
        'profit': [
            r'(?:截至\d{4}年\d{1,2}月\d{1,2}日[^。\n]{0,80}?净利润)[^\n。；;]{0,12}?(-?\d+(?:\.\d+)?)\s*亿',
            r'(?:前三季度[^。\n]{0,80}?净利润)[^\n。；;]{0,12}?(-?\d+(?:\.\d+)?)\s*亿',
            r'(?:Q3[^。\n]{0,80}?净利润)[^\n。；;]{0,12}?(-?\d+(?:\.\d+)?)\s*亿',
        ],
    }
    for pattern in quarter_patterns.get(metric_name, []):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1)), m.group(0)
            except Exception:
                continue
    return None, ''


def detect_profit_range(text):
    # Strip markdown table rows to prevent | pipe chars from causing false matches.
    clean = re.sub(r'^\|.*\|\s*$', '', text, flags=re.MULTILINE)
    patterns = [
        (r'(?:净利润|归母净利润)[^\n。；;|]{0,40}?(-?\d+(?:\.\d+)?)\s*亿[^\n。；;|]{0,12}?(?:至|到|~)\s*(-?\d+(?:\.\d+)?)\s*亿', '亿', False),
        (r'(?:预计|预告)[^\n。；;|]{0,40}?净利润[^\n。；;|]{0,30}?(-?\d+(?:\.\d+)?)\s*亿[^\n。；;|]{0,12}?(?:至|到|~)\s*(-?\d+(?:\.\d+)?)\s*亿', '亿', False),
        (r'(?:亏损|净利润亏损|预计(?:全年)?亏损)[^\n。；;|]{0,20}?(-?\d+(?:\.\d+)?)\s*万(?:元)?[^\n。；;|]{0,12}?(?:至|到|~)\s*(-?\d+(?:\.\d+)?)\s*万(?:元)?', '万', True),
        (r'(?:净利润|归母净利润)[^\n。；;|]{0,40}?(-?\d+(?:\.\d+)?)\s*万(?:元)?[^\n。；;|]{0,12}?(?:至|到|~)\s*(-?\d+(?:\.\d+)?)\s*万(?:元)?', '万', False),
    ]
    for pattern, unit, infer_negative in patterns:
        m = re.search(pattern, clean, re.IGNORECASE)
        if not m:
            continue
        try:
            a = float(m.group(1))
            b = float(m.group(2))
            if infer_negative:
                a = -abs(a)
                b = -abs(b)
            if unit == '万':
                a /= 10000.0
                b /= 10000.0
            return (a, b), m.group(0)
        except Exception:
            continue
    return None, ''


def detect_company_gross_margin(text):
    pattern = re.compile(r'毛利率[^\n。；;]{0,10}?(-?\d+(?:\.\d+)?)%')
    matches = []
    for m in pattern.finditer(text):
        snippet = m.group(0)
        try:
            value = float(m.group(1))
        except Exception:
            continue
        start = max(0, m.start() - 24)
        end = min(len(text), m.end() + 24)
        context = text[start:end]
        matches.append((value, snippet, context))
    if not matches:
        return None, ''
    blockers = [
        '汽车光电子', '光组件', '高端光通讯', '分部', '单项业务', '该业务', '业务毛利率', '产品毛利率',
        '连接器及互连一体化产品', '继电器', '电机与控制组件', '光器件', '光通信器件', '第二增长曲线',
        '主营业务', '业务线', '产品线', '合计', '垫底', '收入撬动', '营收、约'
    ]
    priorities = ['关键指标', '最新财务', '根据', '三季报', '半年报', '整体', '公司', '指标全面承压']
    prioritized = []
    fallback = []
    for value, snippet, context in matches:
        if any(word in context for word in blockers):
            continue
        if any(word in context for word in priorities):
            prioritized.append((value, snippet))
        else:
            fallback.append((value, snippet))
    if prioritized:
        return prioritized[0]
    if fallback:
        return fallback[0]
    return matches[-1][0], matches[-1][1]


def contains_any(text, words):
    return any(word in text for word in words)


def direction_label(code):
    mapping = {'IN': '增持', 'DE': '减持'}
    return mapping.get(str(code or '').upper(), str(code or '未知'))


def normalize_name(name):
    return re.sub(r'\s+', '', str(name or ''))


def sentence_patterns(name):
    base = re.escape(normalize_name(name))
    return {
        'increase': [rf'{base}.{{0,18}}(增持|加仓)', rf'(增持|加仓).{{0,18}}{base}'],
        'decrease': [rf'{base}.{{0,18}}(减持|卖出|套现|出逃)', rf'(减持|卖出|套现|出逃).{{0,18}}{base}'],
    }


def holdertrade_summary(rows, limit=5):
    lines = []
    for row in rows[:limit]:
        lines.append(
            f"{row.get('ann_date') or ''}｜{row.get('holder_name') or ''}｜{direction_label(row.get('in_de'))}｜{int(row.get('change_vol') or 0)}股"
        )
    return '；'.join(lines) if lines else 'holdertrade 无数据'


def build_review(article_text, data, company_name, article_name):
    stock_basic = (data.get('stock_basic') or [{}])[0] if isinstance(data.get('stock_basic'), list) and data.get('stock_basic') else {}
    ts_code = stock_basic.get('ts_code') or '未知代码'
    company_display = company_name or stock_basic.get('name') or '未知公司'

    income = latest_row(data.get('income'))
    indicator = latest_row(data.get('fina_indicator'))
    audit = latest_row(data.get('fina_audit'))
    holdertrade = sorted(data.get('holdertrade') or [], key=lambda r: (str(r.get('ann_date') or ''), str(r.get('holder_name') or '')), reverse=True)
    latest_disclosure = data.get('latest_disclosure') or {}

    issues = []
    revision_items = []
    logic_rows = []
    fact_rows = []
    compliance_rows = []

    def add_issue(problem, advice):
        issues.append(problem)
        revision_items.append((problem, advice))

    latest_period = income.get('end_date') or indicator.get('end_date') or audit.get('end_date') or '未知期间'
    latest_revenue = safe_num(income.get('total_revenue') or income.get('revenue'))
    latest_profit = safe_num(income.get('n_income'))
    latest_gross = safe_num(indicator.get('grossprofit_margin'))
    latest_roe = safe_num(indicator.get('roe'))
    latest_debt = None
    bs = latest_row(data.get('balancesheet'))
    total_assets = safe_num(bs.get('total_assets'))
    total_liab = safe_num(bs.get('total_liab'))
    if total_assets and total_liab is not None and total_assets != 0:
        latest_debt = total_liab / total_assets * 100
    latest_audit = audit.get('audit_result') or '无'

    disclosure_type = latest_disclosure.get('source_type')
    disclosure_level = latest_disclosure.get('source_level') or 'unknown'
    disclosure_metrics = latest_disclosure.get('metrics') or {}
    disclosure_period = latest_disclosure.get('report_period') or ''
    disclosure_revenue = safe_num((disclosure_metrics.get('revenue') or {}).get('value'))
    disclosure_profit_value = safe_num((disclosure_metrics.get('net_profit') or {}).get('value'))
    disclosure_profit_min = safe_num((disclosure_metrics.get('net_profit') or {}).get('min'))
    disclosure_profit_max = safe_num((disclosure_metrics.get('net_profit') or {}).get('max'))
    disclosure_gross = safe_num((disclosure_metrics.get('gross_margin') or {}).get('value'))
    disclosure_roe = safe_num((disclosure_metrics.get('roe') or {}).get('value'))
    disclosure_debt = safe_num((disclosure_metrics.get('debt_ratio') or {}).get('value'))

    if disclosure_level in {'formal_report', 'periodic_report', 'performance_express'}:
        latest_revenue = disclosure_revenue or latest_revenue
        latest_profit = disclosure_profit_value or latest_profit
        latest_gross = disclosure_gross or latest_gross
        latest_roe = disclosure_roe or latest_roe
        latest_debt = disclosure_debt or latest_debt
        if disclosure_period:
            latest_period = disclosure_period

    disclosure_source_text = f"{disclosure_level} / {disclosure_type}" if latest_disclosure else '无'

    if latest_disclosure.get('is_estimate'):
        estimate_words = ['预计', '预告', '区间', '业绩快报', '业绩预告']
        if not contains_any(article_text, estimate_words):
            add_issue('最新口径来自业绩预告/快报，但正文没有明确写出“预计/预告/区间”等边界表达。', '在涉及最新利润结论的句子中明确标注“业绩预告显示/预计/区间”为依据，避免把预测写成正式年报结论。')
    elif disclosure_level == 'formal_report' and contains_any(article_text[:220], ['预计', '预告', '区间']):
        logic_rows.append(('正式披露口径是否被误写成预测', '待人工复核', '当前 latest_disclosure 为正式披露，若标题/导语仍强调“预计/预告/区间”，建议人工复核是否边界过度保守。'))

    disclaimer_hit = contains_any(article_text, ['不构成投资建议', '投资需谨慎', '股市有风险'])
    if disclaimer_hit:
        add_issue('正文手写了免责声明，违反 Skill 里“由 HTML 自动追加”的规则。', '删除正文里的免责声明相关句子，把风险提示留给 HTML 自动页脚。')

    compliance_rows.append(('标题未把预期写成事实', '待人工复核', '脚本无法仅凭正文标题完全判定，需人工确认。'))
    compliance_rows.append(('摘要与导语已写清事实边界', '待人工复核', '脚本未直接校验导语边界表达。'))
    compliance_rows.append(('客户/订单/合作表述未越界', '待人工复核', '涉及语义判断，建议人工二审。'))
    compliance_rows.append(('正文不存在荐股导向', '通过' if not contains_any(article_text, ['建议买入', '建议卖出', '抄底', '还有空间', '值得布局', '目标价']) else '不通过', '存在明显荐股词则直接不通过。'))
    compliance_rows.append(('正文不存在收益承诺或目标价暗示', '通过' if not contains_any(article_text, ['翻倍', '目标价', '收益承诺']) else '不通过', '存在收益承诺或目标价暗示则不通过。'))
    compliance_rows.append(('结尾互动句未鼓动买卖', '通过' if not contains_any(article_text[-300:], ['可以买', '值得买', '还能涨', '抄底']) else '不通过', '结尾不得鼓动交易。'))

    if compliance_rows[3][1] == '不通过':
        add_issue('正文存在明显荐股导向词。', '删除买卖建议、抄底、还有空间、值得布局等表述，改成中性观察句。')
    if compliance_rows[4][1] == '不通过':
        add_issue('正文存在收益承诺或目标价暗示。', '删除翻倍、目标价、收益承诺等字眼，只保留公开信息观察。')
    if compliance_rows[5][1] == '不通过':
        add_issue('结尾互动句存在鼓动交易表述。', '把结尾互动句改成讨论公司经营与风险，不要引导买卖。')

    holder_source = holdertrade_summary(holdertrade)
    recent_dirs = {str(row.get('in_de') or '').upper() for row in holdertrade[:10]}
    article_has_increase = contains_any(article_text, ['增持', '加仓'])
    article_has_decrease = contains_any(article_text, ['减持', '卖出', '出逃', '套现'])
    if article_has_increase and 'IN' not in recent_dirs and 'DE' in recent_dirs:
        add_issue('文章出现“增持/加仓”表述，但近 10 条 holdertrade 记录未见 IN，主要为 DE（减持）。', '把正文里的增持/加仓改为减持，或删除该错误表述。')
    if article_has_decrease and 'DE' not in recent_dirs and 'IN' in recent_dirs:
        add_issue('文章出现“减持/卖出/套现”表述，但近 10 条 holdertrade 记录未见 DE，主要为 IN（增持）。', '把正文里的减持/卖出/套现改为增持，或删除该错误表述。')

    employee_plan_row = next((row for row in holdertrade if '员工持股计划' in str(row.get('holder_name') or '')), None)
    if employee_plan_row:
        expected = direction_label(employee_plan_row.get('in_de'))
        wrong_group = 'increase' if expected == '减持' else 'decrease'
        right_group = 'decrease' if expected == '减持' else 'increase'
        alias_candidates = [
            employee_plan_row.get('holder_name') or '',
            re.sub(r'股份有限公司', '', str(employee_plan_row.get('holder_name') or '')),
            '员工持股计划',
        ]
        wrong_hit = False
        right_hit = False
        for alias in alias_candidates:
            if not alias:
                continue
            patterns = sentence_patterns(alias)
            if any(re.search(p, article_text) for p in patterns[wrong_group]):
                wrong_hit = True
            if any(re.search(p, article_text) for p in patterns[right_group]):
                right_hit = True
        if wrong_hit:
            add_issue(
                f"员工持股计划方向写反：holdertrade 显示 `{employee_plan_row.get('ann_date')}` `{employee_plan_row.get('holder_name')}` 为 {expected}，但正文把它写成了相反方向。",
                f"把“员工持股计划”相关句子改成{expected}表述，并在句子附近标明依据来自 holdertrade。"
            )
        if '员工持股计划' in article_text and not right_hit:
            logic_rows.append(('员工持股计划方向是否准确', '待人工复核', f"源数据最近一条员工持股计划为 {expected}，若正文提到该主体，应明确按 {expected} 表述。"))

    revenue_val, revenue_snippet = detect_quarter_metric(article_text, 'revenue')
    if revenue_val is None:
        # Try specific total-revenue patterns first (e.g. "营收：5.96亿" without a sub-business modifier),
        # then fall back to generic match excluding lines that look like sub-business breakdowns.
        revenue_val, revenue_snippet = detect_metric(article_text, [
            r'(?<![^\n])- \*\*营收[：:]\s*(-?\d+(?:\.\d+)?)\s*亿',  # "- **营收：X亿**" list item format
            r'(?:总营收|营业收入)[^\n。；;]{0,20}?(-?\d+(?:\.\d+)?)\s*亿',
        ])
    if revenue_val is None:
        # Generic fallback: match "营收" but skip if preceded by business-name modifiers
        for m in re.finditer(r'(?:营收|营业收入)[^\n。；;]{0,20}?(-?\d+(?:\.\d+)?)\s*亿', article_text):
            context_start = max(0, m.start() - 20)
            context = article_text[context_start:m.start()]
            # Skip sub-business revenue like "机器人业务营收", "模具制造营收"
            if re.search(r'(?:业务|制造|产品|板块)\*{0,2}$', context):
                continue
            try:
                revenue_val = float(m.group(1))
                revenue_snippet = m.group(0)
            except Exception:
                continue
            break

    profit_val, profit_snippet = detect_quarter_metric(article_text, 'profit')
    if profit_val is None:
        profit_val, profit_snippet = detect_metric(article_text, [r'(?:净利润|归母净利润)[^\n。；;]{0,20}?(-?\d+(?:\.\d+)?)\s*亿'])
    profit_range, profit_range_snippet = detect_profit_range(article_text)
    gross_val, gross_snippet = detect_company_gross_margin(article_text)
    roe_val, roe_snippet = detect_metric(article_text, [r'ROE[^\n。；;]{0,10}?(-?\d+(?:\.\d+)?)%'])
    debt_val, debt_snippet = detect_metric(article_text, [r'资产负债率[^\n。；;]{0,10}?(-?\d+(?:\.\d+)?)%'])

    def metric_check(name, article_value, source_value, unit, snippet, threshold_abs=None, threshold_ratio=None):
        if article_value is None:
            return '未提取'
        if source_value is None:
            return '待人工核对'
        mismatch = False
        if threshold_abs is not None and abs(article_value - source_value) > threshold_abs:
            mismatch = True
        if threshold_ratio is not None and source_value != 0 and abs(article_value - source_value) / abs(source_value) > threshold_ratio:
            mismatch = True
        if mismatch:
            add_issue(f'{name} 与 financial_data.json 最新口径不一致：正文 `{snippet}`，源数据 `{source_value:.2f}{unit}`。', f'将该处数值改为最新口径 `{source_value:.2f}{unit}`，并确保上下文结论同步更新。')
            return '不通过'
        return '通过'

    revenue_pass = metric_check('最新营收', revenue_val, (latest_revenue / 1e8) if latest_revenue is not None else None, '亿', revenue_snippet, threshold_abs=0.1, threshold_ratio=0.01)
    forecast_profit_pass = '未检查'
    forecast_profit_source = ''
    if latest_disclosure.get('is_estimate') and disclosure_profit_min is not None and disclosure_profit_max is not None:
        min_profit = disclosure_profit_min
        max_profit = disclosure_profit_max
        forecast_profit_source = f'{disclosure_period or latest_period} / {fmt_yi(min_profit)} ~ {fmt_yi(max_profit)}'
        if profit_range and min_profit is not None and max_profit is not None:
            article_min, article_max = sorted(profit_range)
            source_min, source_max = sorted((min_profit / 1e8, max_profit / 1e8))
            if abs(article_min - source_min) <= 0.2 and abs(article_max - source_max) <= 0.2 and contains_any(article_text, ['预计', '预告', '区间']):
                forecast_profit_pass = '通过'
            else:
                add_issue(
                    f'最新净利润来自业绩预告区间，但正文区间 `{profit_range_snippet or profit_snippet}` 与源数据 `{fmt_yi(min_profit)} ~ {fmt_yi(max_profit)}` 不一致，或缺少“预计/预告”表达。',
                    '把正文改成区间写法，并明确标注“业绩预告显示/预计净利润为 xx~xx 亿”。'
                )
                forecast_profit_pass = '不通过'
        else:
            add_issue(
                f'最新净利润来自业绩预告区间 `{fmt_yi(min_profit)} ~ {fmt_yi(max_profit)}`，但正文没有明确写出区间。',
                '在正文里明确写出“预计净利润 xx~xx 亿”，不要只写单个数字。'
            )
            forecast_profit_pass = '不通过'

    latest_profit_yi = (latest_profit / 1e8) if latest_profit is not None else None
    if profit_val is not None and latest_profit_yi is not None and abs(profit_val - latest_profit_yi) <= 0.2:
        profit_pass = '通过'
    else:
        profit_pass = metric_check('最新季报净利润', profit_val, latest_profit_yi, '亿', profit_snippet, threshold_abs=0.1, threshold_ratio=0.01)
    gross_pass = metric_check('毛利率', gross_val, latest_gross, '%', gross_snippet, threshold_abs=1.0)
    roe_pass = metric_check('ROE', roe_val, latest_roe, '%', roe_snippet, threshold_abs=1.0)
    debt_pass = metric_check('资产负债率', debt_val, latest_debt, '%', debt_snippet, threshold_abs=1.0)

    fact_rows.extend([
        ('最新披露来源等级', disclosure_source_text, f"{disclosure_period or latest_period}", '通过', '该项为信息性展示，用于区分正式披露/定期报告/快报/预告'),
        ('最新口径营收', revenue_snippet or '未识别到', f'{latest_period} / {fmt_yi(latest_revenue)}', revenue_pass, '与最新口径不一致则回写正文'),
        ('最新季报净利润', profit_snippet or '未识别到', f'{latest_period} / {fmt_yi(latest_profit) if latest_profit is not None else "无"}', profit_pass, '与最新季报口径不一致则回写正文'),
        ('毛利率', gross_snippet or '未识别到', f'{latest_period} / {fmt_pct(latest_gross)}', gross_pass, '与最新口径不一致则回写正文'),
        ('ROE', roe_snippet or '未识别到', f'{latest_period} / {fmt_pct(latest_roe)}', roe_pass, '与最新口径不一致则回写正文'),
        ('资产负债率', debt_snippet or '未识别到', f'{latest_period} / {fmt_pct(latest_debt)}', debt_pass, '与最新口径不一致则回写正文'),
        ('审计意见', latest_audit, 'fina_audit', '通过', '如正文未体现审计边界，可补一句'),
        ('股东增减持结论', '正文包含增减持相关表述' if contains_any(article_text, ['增持', '减持', '加仓', '出逃', '套现']) else '正文未写', holder_source + '（IN=增持，DE=减持）', '不通过' if any('holdertrade' in issue or '员工持股计划方向写反' in issue or '增持/加仓' in issue or '减持/卖出/套现' in issue for issue in issues) else '通过', '方向错了就必须改，员工持股计划要按主体逐句核'),
    ])
    if latest_disclosure.get('is_estimate') and disclosure_profit_min is not None and disclosure_profit_max is not None:
        fact_rows.insert(
            2,
            (
                '全年业绩预告净利润区间',
                profit_range_snippet or '未识别到',
                forecast_profit_source,
                forecast_profit_pass,
                '若来源为业绩预告，正文必须写成预计区间，并明确标注“预计/预告”'
            ),
        )

    logic_rows.append(('是否存在因果倒置', '待人工复核', '需要结合全文语义人工判断。'))
    logic_rows.append(('是否存在前后矛盾', '待人工复核', '建议人工通读二审。'))
    logic_rows.append(('是否把推测写成事实', '待人工复核', '重点看客户/订单/合作表述。'))
    logic_rows.append(('最新披露来源等级是否与正文写法匹配', '通过' if disclosure_source_text != '无' else '待人工复核', 'formal_report/periodic_report 可用确定口吻；performance_forecast 必须保留预计/区间表达。'))
    logic_rows.append(('数据与结论是否匹配', '不通过' if any(r[3] == '不通过' for r in fact_rows) else '通过', '关键数据已做机械比对；如有不通过必须改稿。'))
    logic_rows.append(('是否仍有八股平铺感', '待人工复核', '属风格项，需人工审读。'))

    pending_manual_count = sum(1 for _, status, _ in compliance_rows + logic_rows if status == '待人工复核')
    missing_metric_count = sum(1 for row in fact_rows if row[3] in ('未提取', '待人工核对'))

    critical_labels = {'最新口径营收', '最新季报净利润', '全年业绩预告净利润区间', '毛利率', 'ROE', '资产负债率'}
    critical_failures = [row[0] for row in fact_rows if row[0] in critical_labels and row[3] in ('未提取', '待人工核对', '待人工复核', '不通过')]

    if critical_failures:
        add_issue('关键财务项未通过核验（' + '、'.join(critical_failures) + '）', '关键财务项只要出现未提取、待人工核对、待人工复核或不通过，就必须先修正文稿，再重新运行审稿。')

    if missing_metric_count >= 2:
        add_issue(f'关键财务项提取不足（{missing_metric_count} 项），当前正文对营收/利润/毛利率/ROE/资产负债率覆盖不完整。', '至少补齐营收、净利润，以及毛利率/ROE/资产负债率中的 2 项，并确保有明确数字或明确说明未披露。')

    has_hard_failures = bool(critical_failures) or any(row[1] == '不通过' for row in compliance_rows) or any(row[1] == '不通过' for row in logic_rows)

    if pending_manual_count >= 8 and has_hard_failures:
        add_issue(f'待人工复核项过多（{pending_manual_count} 项），当前稿件仍缺少足够自动可验证的事实边界与表达收敛。', '补充更明确的事实边界、减少模糊判断，并在关键结论附近补足可核对的数据支撑后再复审。')

    verdict = '可发布' if not issues else '需重写'
    allow_publish = '允许' if verdict == '可发布' else '不允许'
    rewritten = '否' if issues else '是'

    lines = []
    lines.append(f'# 审稿复核报告：{company_display}（{ts_code}）')
    lines.append('')
    lines.append(f'**审稿时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}')
    lines.append('**审稿人**：Deterministic Review Gate')
    lines.append(f'**文章文件**：{article_name}')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 一、审稿结论')
    lines.append('')
    lines.append(f'**结论：{verdict}**')
    lines.append('')
    lines.append(f'- 本轮审稿发现的问题：{len(issues)} 处')
    if issues:
        for issue in issues:
            lines.append(f'- {issue}')
    else:
        lines.append('- 未发现脚本可识别的硬错误。')
    lines.append(f'- 是否已完成重写：{rewritten}')
    lines.append(f'- 当前版本是否允许继续配图和推草稿箱：{allow_publish}')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 二、逐条修订意见')
    lines.append('')
    if revision_items:
        for idx, item in enumerate(revision_items, start=1):
            lines.append(f'{idx}. 问题：{item[0]}')
            lines.append(f'   修改意见：{item[1]}')
    else:
        lines.append('1. 未发现脚本可识别的硬错误，无需逐条修订。')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 三、关键事实核对清单')
    lines.append('')
    lines.append('| 核对项 | 原文表述 | 依据来源 | 是否通过 | 不通过如何修改 |')
    lines.append('|--------|---------|---------|---------|---------------|')
    for row in fact_rows:
        lines.append('| ' + ' | '.join(str(x).replace('|', '\\|') for x in row) + ' |')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 四、财经号合规写法清单')
    lines.append('')
    lines.append('| 检查项 | 是否通过 | 说明 |')
    lines.append('|--------|---------|------|')
    for row in compliance_rows:
        lines.append('| ' + ' | '.join(str(x).replace('|', '\\|') for x in row) + ' |')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 五、逻辑与表达检查')
    lines.append('')
    lines.append('| 检查项 | 是否通过 | 说明 |')
    lines.append('|--------|---------|------|')
    for row in logic_rows:
        lines.append('| ' + ' | '.join(str(x).replace('|', '\\|') for x in row) + ' |')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 六、最终处理意见')
    lines.append('')
    if issues:
        lines.append('- 必须先改 `article.md`，再重新运行审稿。')
        lines.append('- 至少修正所有“不通过”的数据项与增减持方向错误。')
        lines.append('- `holdertrade` 方向必须按 `IN=增持`、`DE=减持` 翻译，尤其是“员工持股计划”这类主体要逐句核对。')
        lines.append('- `待人工复核` 超过阈值时，不允许直接放行。')
        lines.append('- 在 `review.md` 明确写出“可发布”之前，不允许继续配图、生成 HTML、推送草稿箱。')
    else:
        lines.append('- 只有当硬错误为 0，且待人工复核项不过多、关键财务覆盖完整时，才允许进入后续配图、HTML 和草稿箱流程。')
    lines.append('')
    return '\n'.join(lines) + '\n', verdict


def main():
    if len(sys.argv) < 4:
        print('用法: review_article.py <article.md> <financial_data.json> <review.md> [公司名]', file=sys.stderr)
        sys.exit(1)

    article_path = Path(sys.argv[1])
    data_path = Path(sys.argv[2])
    review_path = Path(sys.argv[3])
    company_name = sys.argv[4] if len(sys.argv) > 4 else ''

    article_text = article_path.read_text(encoding='utf-8')
    data = load_json(data_path)
    review_text, verdict = build_review(article_text, data, company_name, article_path.name)
    review_path.write_text(review_text, encoding='utf-8')
    print(f'✅ review.md 已生成: {review_path}')
    print(f'审稿结论: {verdict}')
    if verdict != '可发布':
        sys.exit(2)


if __name__ == '__main__':
    main()
