#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 Tavily 生成外部风险信号底稿。

用法：
  python3 scripts/generate_external_risk_brief.py <financial_data.json> <external_risk_brief.md> [company_name]
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, '/root/.openclaw/workspace/lib')
from tavily_pool import get_routed_key, mark_key_error, mark_key_success


EXCLUDE_PATTERNS = [
    '公司章程', '百科', '简介', '招聘', 'boss直聘', '猎聘', '脉脉', '天眼查', '企查查', '爱企查',
    '年度报告全文', '半年度报告全文', '季度报告全文', '三季度报告全文', '年报全文', '半年报全文',
]

OFFICIAL_DOMAIN_BONUS = {
    'cninfo.com.cn': 6,
    'static.cninfo.com.cn': 6,
    'hkexnews.hk': 6,
    'hkex.com.hk': 5,
    'sse.com.cn': 5,
    'szse.cn': 5,
    'csrc.gov.cn': 5,
    'creditchina.gov.cn': 4,
    'zxgk.court.gov.cn': 5,
}

TAVILY_TIMEOUT_SECONDS = 30
TAVILY_MAX_RESULTS = 6
QUERY_PARALLELISM = 4
EARLY_STOP_RESULT_COUNT = 3
EARLY_STOP_HIT_QUERIES = 2
QUERY_PER_THEME_LIMIT = 6

MEDIA_DOMAIN_BONUS = {
    'finance.sina.com.cn': 3,
    'eastmoney.com': 3,
    'cls.cn': 3,
    'stcn.com': 3,
    'ifeng.com': 2,
    'caixin.com': 3,
    '21jingji.com': 3,
    'wallstreetcn.com': 3,
    'gelonghui.com': 2,
    'finet.hk': 2,
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def latest_row(rows):
    if not isinstance(rows, list) or not rows:
        return {}
    def key_fn(row):
        return (str(row.get('end_date') or ''), str(row.get('ann_date') or ''))
    return sorted(rows, key=key_fn, reverse=True)[0]


def detect_market(stock_basic):
    ts_code = str((stock_basic or {}).get('ts_code') or '').upper()
    if ts_code.endswith('.HK'):
        return 'HK'
    return 'CN'


def normalize_text(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def build_aliases(company, stock_basic, latest_disclosure=None):
    aliases = []
    disclosure_title = ''
    if isinstance(latest_disclosure, dict):
        disclosure_title = str(latest_disclosure.get('source_title') or '')
    seed_values = [
        company,
        stock_basic.get('name') if isinstance(stock_basic, dict) else '',
        stock_basic.get('ts_code') if isinstance(stock_basic, dict) else '',
        disclosure_title,
    ]
    for raw in seed_values:
        raw = str(raw or '').strip()
        if not raw:
            continue
        aliases.append(raw)
        aliases.append(raw.replace('.SZ', '').replace('.SH', '').replace('.HK', ''))
        aliases.append(re.sub(r'^(?:\*?ST)', '', raw).strip())
        aliases.append('ST' + re.sub(r'^(?:\*?ST)', '', raw).strip())
        for suffix in ['股份有限公司', '有限公司', '集团股份有限公司', '集团有限公司', '集团', '第三季度报告', '半年度报告', '年度报告']:
            if raw.endswith(suffix):
                aliases.append(raw[:-len(suffix)])
    cleaned = []
    for alias in aliases:
        alias = str(alias).strip()
        alias = re.sub(r'\s+', '', alias)
        if len(alias) >= 2 and alias not in cleaned:
            cleaned.append(alias)
    return cleaned


def extract_stock_code(stock_basic):
    ts_code = str((stock_basic or {}).get('ts_code') or '').upper()
    return ts_code.replace('.SZ', '').replace('.SH', '').replace('.HK', '')


def extract_related_entities_from_profile(path_value):
    value = str(path_value or '').strip()
    if not value:
        return []
    path = Path(value)
    if not path.exists() or path.is_dir():
        return []
    text = path.read_text(encoding='utf-8')
    start = text.find('## 四、潜在关联主体')
    if start == -1:
        return []
    tail = text[start:]
    next_header = tail.find('\n## ', 5)
    section = tail if next_header == -1 else tail[:next_header]

    related = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith('- '):
            continue
        name = line[2:].strip()
        if not name or '暂未自动识别' in name:
            continue
        if '来源：' in name or '摘要：' in name:
            continue
        if len(name) >= 2 and name not in related:
            related.append(name)
    return related[:8]


def merge_aliases(base_aliases, extra_aliases):
    merged = []
    for group in [base_aliases, extra_aliases]:
        for item in group or []:
            val = str(item or '').strip()
            if len(val) >= 2 and val not in merged:
                merged.append(val)
    return merged


def prioritize_related_entities(entities):
    def score(name):
        name = str(name or '')
        group_bonus = 0
        if '集团' in name or '油脂工业有限公司' in name:
            group_bonus += 10
        if '中国信达' in name or '华融' in name or '东方资产' in name or '长城资产' in name:
            group_bonus += 5
        return (-group_bonus, len(name))

    uniq = []
    for item in entities or []:
        name = str(item or '').strip()
        if len(name) >= 2 and name not in uniq:
            uniq.append(name)
    return sorted(uniq, key=score)


def extract_stock_code(stock_basic):
    ts_code = str((stock_basic or {}).get('ts_code') or '').upper()
    return ts_code.replace('.SZ', '').replace('.SH', '').replace('.HK', '')


def tavily_search(query: str, max_results: int = TAVILY_MAX_RESULTS, days: int = 365 * 5):
    key = get_routed_key(route='china_hk_finance', query=query)
    if not key:
        return []

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    payload = {
        'query': query,
        'search_depth': 'advanced',
        'include_answer': False,
        'include_raw_content': False,
        'max_results': max_results,
        'date_range': {
            'start': start_date.strftime('%Y-%m-%d'),
            'end': end_date.strftime('%Y-%m-%d'),
        },
    }
    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post('https://api.tavily.com/search', headers=headers, json=payload, timeout=TAVILY_TIMEOUT_SECONDS)
        if resp.status_code == 200:
            mark_key_success(key)
            return resp.json().get('results', []) or []
        mark_key_error(key, f'HTTP {resp.status_code}')
        return []
    except Exception as e:
        mark_key_error(key, str(e))
        return []


def get_domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ''


def result_title(item):
    return normalize_text(item.get('title', ''))


def result_content(item):
    return normalize_text(item.get('content', ''))


def is_excluded(item):
    text = (result_title(item) + ' ' + result_content(item)).lower()
    return any(keyword.lower() in text for keyword in EXCLUDE_PATTERNS)


def domain_bonus(domain):
    for host, score in OFFICIAL_DOMAIN_BONUS.items():
        if host in domain:
            return score, 'official'
    for host, score in MEDIA_DOMAIN_BONUS.items():
        if host in domain:
            return score, 'media'
    return 0, 'other'


def score_result(item, aliases, keywords, alias_mode='strict'):
    title = result_title(item)
    content = result_content(item)
    url = str(item.get('url') or '')
    domain = get_domain(url)
    alias_haystack = (title + ' ' + url).lower()
    relaxed_alias_haystack = (title + ' ' + content[:320] + ' ' + url).lower()
    keyword_haystack = relaxed_alias_haystack

    haystack_for_alias = relaxed_alias_haystack if alias_mode == 'relaxed' else alias_haystack
    alias_hit = any(alias.lower() in haystack_for_alias for alias in aliases) if aliases else True
    stock_code_hit = any(code in keyword_haystack for code in re.findall(r'\d{6}', ' '.join(aliases or [])))
    if not alias_hit and not stock_code_hit:
        return None

    keyword_hits = 0
    for keyword in keywords or []:
        low = keyword.lower()
        if low in title.lower():
            keyword_hits += 2
        elif low in keyword_haystack:
            keyword_hits += 1
    if keywords and keyword_hits == 0:
        return None

    score = 0
    alias_score = 0
    for alias in aliases or []:
        low = alias.lower()
        if low in title.lower():
            alias_score = max(alias_score, 6)
        elif low in url.lower():
            alias_score = max(alias_score, 5)
    score += alias_score
    score += keyword_hits

    d_bonus, d_kind = domain_bonus(domain)
    score += d_bonus
    if url.lower().endswith('.pdf'):
        score += 1

    item = dict(item)
    item['_score'] = score
    item['_domain_kind'] = d_kind
    item['_domain'] = domain
    return item


def filter_results(results, aliases, keywords, min_score=7, alias_mode='strict'):
    scored = []
    seen = set()
    for item in results:
        if not item or not item.get('url'):
            continue
        if item['url'] in seen:
            continue
        if is_excluded(item):
            continue
        scored_item = score_result(item, aliases, keywords, alias_mode)
        if not scored_item:
            continue
        if scored_item['_score'] < min_score:
            continue
        seen.add(item['url'])
        scored.append(scored_item)
    scored.sort(key=lambda x: (x.get('_score', 0), x.get('url', '')), reverse=True)
    return scored[:4]




def build_name_variants(company, stock_code=''):
    variants = []
    company = normalize_text(company)
    stock_code = normalize_text(stock_code)
    if company:
        variants.append(company)
        clean = re.sub(r'^(?:\*?ST)', '', company).strip()
        if clean and clean not in variants:
            variants.append(clean)
        st_name = f'ST{clean}' if clean else ''
        if st_name and st_name not in variants:
            variants.append(st_name)
    if stock_code and stock_code not in variants:
        variants.append(stock_code)
    return variants


def expand_query_variants(company, stock_code, template):
    out = []
    for anchor in build_name_variants(company, stock_code):
        q = template.format(anchor=anchor).strip()
        if q and q not in out:
            out.append(q)
    return out

def build_queries(company, market, stock_code='', related_entities=None):
    query_defs = [
        {
            'theme': '控制权与股东风险',
            'query_templates': [
                '{anchor} 控股股东 质押 冻结 被动减持 控制权变更 公告',
                '{anchor} 解除质押 继续质押 减持 公告',
            ],
            'keywords': ['质押', '冻结', '控制权', '减持', '解除质押', '控股股东'],
            'evidence': 'B',
            'min_score': 6,
        },
        {
            'theme': '监管与法律风险',
            'query_templates': [
                '{anchor} 立案调查 行政处罚 问询函 关注函 公告',
                '{anchor} 诉讼 被执行 失信 资产冻结 裁判文书',
                '{anchor} 证监会 立案 信披违法违规 财务造假',
            ],
            'keywords': ['立案', '处罚', '问询', '关注函', '诉讼', '被执行', '失信', '冻结', '证监会', '信披违法违规', '财务造假'],
            'evidence': 'B',
            'min_score': 6,
            'alias_mode': 'relaxed',
        },
        {
            'theme': '信用与票据风险',
            'query_templates': [
                '{anchor} 债务违约 债务展期 评级下调',
                '{anchor} 商票逾期 承兑异常 票据逾期',
            ],
            'keywords': ['违约', '展期', '评级下调', '商票', '承兑', '票据', '逾期'],
            'evidence': 'B',
            'min_score': 6,
            'alias_mode': 'relaxed',
        },
        {
            'theme': '集团/控股股东债务与纾困链',
            'query_templates': [
                '{anchor} 控股股东 集团 债务 违约 展期 评级下调',
                '{anchor} 母公司 集团 中国信达 华融 AMC 纾困',
                '{anchor} 集团 互保 担保 资金链',
            ],
            'keywords': ['控股股东', '集团', '债务', '违约', '展期', '评级下调', '中国信达', '华融', 'amc', '纾困', '互保', '担保', '资金链'],
            'evidence': 'B/C',
            'min_score': 6,
            'alias_mode': 'relaxed',
        },
        {
            'theme': '经营异常与审计边界',
            'query_templates': [
                '{anchor} 停产 减产 关厂 重大减值',
                '{anchor} 审计意见 持续经营重大不确定性 保留意见 无法表示意见',
                '{anchor} ST *ST 退市风险警示 审计意见 无法表示意见 保留意见',
            ],
            'keywords': ['停产', '减产', '关厂', '减值', '审计意见', '持续经营', '保留意见', '无法表示意见', 'ST', '退市风险警示', '*ST'],
            'evidence': 'B',
            'min_score': 6,
            'alias_mode': 'relaxed',
        },
        {
            'theme': '港股退市/私有化/纾困链',
            'query_templates': [
                '{anchor} 退市 私有化 协议安排 联合公告',
                '{anchor} 纾困 债务展期 中国信达 华融 AMC',
                '{anchor} delisting privatization scheme of arrangement debt restructuring',
            ],
            'keywords': ['退市', '私有化', '协议安排', '联合公告', '纾困', '中国信达', '华融', 'delisting', 'privatization', 'scheme'],
            'evidence': 'B/C',
            'min_score': 7,
            'alias_mode': 'relaxed',
        },
    ]

    expanded = []
    for item in query_defs:
        built_queries = []
        for tpl in item['query_templates']:
            built_queries.extend(expand_query_variants(company, stock_code, tpl))
        copied = dict(item)
        copied['queries'] = []
        for q in built_queries:
            if q not in copied['queries']:
                copied['queries'].append(q)
        expanded.append(copied)

    if related_entities:
        for entity in prioritize_related_entities(related_entities)[:3]:
            expanded.append({
                'theme': f'关联主体风险链：{entity}',
                'queries': [
                    f'{entity} 债务 违约 展期 评级下调',
                    f'{entity} 纾困 中国信达 华融 AMC',
                    f'{entity} 互保 担保 资金链',
                ],
                'keywords': ['债务', '违约', '展期', '评级下调', '纾困', '中国信达', '华融', 'AMC', '互保', '担保', '资金链'],
                'evidence': 'B/C',
                'min_score': 6,
                'alias_mode': 'relaxed',
                'aliases': [entity],
            })

    if market == 'HK':
        expanded.append({
            'theme': '港股官方文件补充线索',
            'queries': expand_query_variants(company, stock_code, '{anchor} HKEXnews scheme of arrangement delisting suspension resumption guidance'),
            'keywords': ['hkex', 'scheme', 'delisting', 'resumption', 'suspension', 'announcement'],
            'evidence': 'B',
            'min_score': 8,
        })
    return expanded


def summarize_results(results):
    if not results:
        return []
    lines = []
    for item in results:
        title = result_title(item)
        url = item.get('url', '')
        content = result_content(item)[:180]
        lines.append(f'- {title}\n  来源：{url}\n  相关性：{item.get("_score", 0)} 分 / 域名类型：{item.get("_domain_kind", "other")}\n  摘要：{content}')
    return lines


def collect_results_for_item(item, aliases):
    queries = list(dict.fromkeys((item.get('queries') or [])[:QUERY_PER_THEME_LIMIT]))
    if not queries:
        return []

    alias_candidates = merge_aliases(aliases, item.get('aliases'))
    merged_results = []
    seen = set()
    hit_queries = 0
    executor = ThreadPoolExecutor(max_workers=max(1, min(QUERY_PARALLELISM, len(queries))))
    futures = {executor.submit(tavily_search, query): query for query in queries}
    try:
        for future in as_completed(futures, timeout=max(TAVILY_TIMEOUT_SECONDS + 2, 12)):
            try:
                raw_results = future.result() or []
            except Exception:
                raw_results = []
            filtered = filter_results(
                raw_results,
                alias_candidates,
                item['keywords'],
                item.get('min_score', 7),
                item.get('alias_mode', 'strict'),
            )
            if filtered:
                hit_queries += 1
            for result in filtered:
                if result['url'] in seen:
                    continue
                seen.add(result['url'])
                merged_results.append(result)
            if len(merged_results) >= EARLY_STOP_RESULT_COUNT or hit_queries >= EARLY_STOP_HIT_QUERIES:
                break
        merged_results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        return merged_results[:4]
    except FuturesTimeoutError:
        merged_results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        return merged_results[:4]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def risk_hint(theme, results):
    if not results:
        return None
    titles = '；'.join(result_title(r) for r in results[:2])
    if theme == '港股退市/私有化/纾困链':
        return {
            'theme': theme,
            'fact': f'外部检索已命中与退市、私有化、纾困或债务展期相关的公开线索：{titles}。',
            'direct': '这意味着公司可能存在一条“结果事件 -> 交易安排/纾困 -> 债务或控制权压力”的风险链，值得继续核查。',
            'status': '需与港交所公告、公司通函、评级材料交叉验证后再写成正文结论。',
            'evidence': 'B/C',
        }
    return {
        'theme': theme,
        'fact': f'外部检索已命中相关公开线索：{titles}。',
        'direct': '说明该维度存在继续深挖的外部风险信号，不应只依赖财务底稿。',
        'status': '需与公告、问询函、法院或评级材料交叉验证后决定写作权重。',
        'evidence': 'B',
    }






def build_structured_risk_cards(data, company):
    cards = []

    latest = data.get('latest_disclosure') or {}
    metrics = latest.get('metrics') or {}
    np = metrics.get('net_profit') or {}
    if latest.get('is_estimate') and isinstance(np, dict) and np.get('min') is not None and np.get('max') is not None:
        min_yi = np.get('min') / 1e8
        max_yi = np.get('max') / 1e8
        cards.append({
            'theme': '全年业绩预亏线索',
            'fact': f'{company} 最新业绩预告显示，全年净利润预计区间为 {min_yi:.2f}亿 至 {max_yi:.2f}亿。',
            'direct': '这说明公司全年盈利能力明显承压，且预告已把年度口径从“季报盈利”切换到“全年预亏”框架。',
            'status': '正文应明确写成“预计/区间/预告显示”，并结合前三季度数据解释四季度利润转负的原因。',
            'evidence': 'A',
        })

    audits = data.get('fina_audit') or []
    if isinstance(audits, list) and audits:
        risky = []
        for row in audits[:5]:
            opinion = normalize_text((row or {}).get('audit_result') or (row or {}).get('audit_opinion') or (row or {}).get('opinion') or '')
            end_date = str((row or {}).get('end_date') or '')
            if opinion and ('保留' in opinion or '无法表示' in opinion or '否定' in opinion):
                risky.append((end_date, opinion))
        if risky:
            sample = '；'.join([f'{d}:{o}' for d, o in risky[:3]])
            cards.append({
                'theme': '非标审计意见线索',
                'fact': f'结构化财务数据已显示近年存在非标审计意见：{sample}。',
                'direct': '连续非标审计往往意味着财务真实性、持续经营能力或关键会计处理存在重大不确定性。',
                'status': '正文应把“审计边界”写成核心风险，而不是只在结尾轻描淡写。',
                'evidence': 'A',
            })

    holdertrade = data.get('holdertrade') or []
    if isinstance(holdertrade, list) and holdertrade:
        recent = holdertrade[:20]
        dec = [r for r in recent if str((r or {}).get('in_de') or '').upper() == 'DE']
        inc = [r for r in recent if str((r or {}).get('in_de') or '').upper() == 'IN']
        dec_vol = sum(abs(float((r or {}).get('change_vol') or 0)) for r in dec)
        if len(dec) >= 3 and len(dec) > len(inc):
            cards.append({
                'theme': '股东减持线索',
                'fact': f'最近 {len(recent)} 条股东变动记录中，减持 {len(dec)} 次、增持 {len(inc)} 次，减持规模合计约 {dec_vol:.0f} 股。',
                'direct': '股东持续减持通常会放大市场对基本面、流动性和治理稳定性的担忧。',
                'status': '正文应谨慎写成“股东近期有所减持，市场情绪承压”，避免写成未核实的主观动机。',
                'evidence': 'A/B',
            })

    return cards

def build_fallback_cards(data, company):
    cards = []
    latest = data.get('latest_disclosure') or {}
    if latest:
        title = str(latest.get('source_title') or '').strip()
        period = str(latest.get('report_period') or '').strip()
        source_url = str(latest.get('source_url') or '').strip()
        metrics = latest.get('metrics') or {}
        revenue = metrics.get('revenue', {}).get('value') if isinstance(metrics.get('revenue'), dict) else None
        net_profit = metrics.get('net_profit', {}).get('value') if isinstance(metrics.get('net_profit'), dict) else None
        fact = f'当前至少可确认一份最新披露材料：{title or company}（期别 {period or "未注明"}）。'
        direct = '外部检索源未召回结果时，至少应把最近一期公告中的关键异常变化列为待核查风险。'
        if revenue is not None or net_profit is not None:
            direct += f' 当前可见营收={revenue}、净利润={net_profit}。'
        cards.append({
            'theme': '最新披露兜底线索',
            'fact': fact,
            'direct': direct,
            'status': f'优先回到公告原文核验：{source_url}' if source_url else '优先回到公告原文核验。',
            'evidence': 'A/B',
        })

    audits = data.get('fina_audit') or []
    risky = []
    if isinstance(audits, list):
        for row in audits[:5]:
            opinion = normalize_text((row or {}).get('audit_result') or (row or {}).get('audit_opinion') or (row or {}).get('opinion') or '')
            end_date = str((row or {}).get('end_date') or '')
            if opinion and ('保留' in opinion or '无法表示' in opinion or '否定' in opinion):
                risky.append((end_date, opinion))
    if risky:
        sample = '；'.join([f'{d}:{o}' for d, o in risky[:3]])
        cards.append({
            'theme': '非标审计意见兜底线索',
            'fact': f'结构化财务数据已显示近年存在非标审计意见：{sample}。',
            'direct': '这通常意味着财务真实性、持续经营能力或重大事项核验存在显著风险，应进入正文核心风险段。',
            'status': '需回到审计报告原文、年报问询函和后续更正公告交叉核验。',
            'evidence': 'A',
        })
    return cards

def main():
    if len(sys.argv) < 3:
        raise SystemExit('usage: generate_external_risk_brief.py <financial_data.json> <external_risk_brief.md> [company_name] [company_profile_brief.md]')

    data_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    company_arg = sys.argv[3] if len(sys.argv) > 3 else ''
    profile_brief_arg = sys.argv[4] if len(sys.argv) > 4 else ''
    data = load_json(data_path)
    stock_basic = latest_row(data.get('stock_basic') or [])
    company = company_arg or stock_basic.get('name') or '目标公司'
    market = detect_market(stock_basic)
    aliases = build_aliases(company, stock_basic, data.get('latest_disclosure') or {})
    related_entities = extract_related_entities_from_profile(profile_brief_arg)
    stock_code = str((stock_basic or {}).get('ts_code') or '').upper()
    stock_code = stock_code.replace('.SZ', '').replace('.SH', '').replace('.HK', '')

    query_defs = build_queries(company, market, stock_code, related_entities)
    sections = []
    signal_cards = []

    for item in query_defs:
        merged_results = collect_results_for_item(item, aliases)
        if not merged_results:
            continue
        sections.append((item, merged_results))
        hint = risk_hint(item['theme'], merged_results)
        if hint:
            signal_cards.append(hint)

    lines = [
        f'# 外部风险底稿：{company}',
        '',
        f'- 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        '- 数据来源：Tavily 定向外部检索',
        f'- 市场识别：{"港股/港股历史链" if market == "HK" else "A股/内地公开披露链"}',
        f'- 关联主体：{"、".join(related_entities) if related_entities else "未识别"}',
        '- 说明：本底稿用于补充外部风险信号，结论默认属于 B/C 级证据，写入正文前应优先与公告、交易所、法院、评级材料交叉验证。',
        '',
        '## 一、外部风险信号总览',
        '',
    ]

    structured_cards = build_structured_risk_cards(data, company)
    fallback_cards = (structured_cards + build_fallback_cards(data, company)) if not signal_cards else []
    if signal_cards:
        for card in signal_cards:
            lines.append(f"- [{card['evidence']}级线索] {card['theme']}：{card['fact']}")
    elif fallback_cards:
        for card in fallback_cards:
            lines.append(f"- [{card['evidence']}级兜底] {card['theme']}：{card['fact']}")
    else:
        lines.append('- 当前外部检索未命中足够强的高价值风险线索，后续可人工扩大关键词或延长时间轴。')

    lines.extend(['', '## 二、外部风险卡片', ''])
    card_source = signal_cards or fallback_cards
    if card_source:
        for idx, card in enumerate(card_source, 1):
            lines.extend([
                f"### {idx}. {card['theme']}",
                '',
                f"- 结果事实：{card['fact']}",
                f"- 直接判断：{card['direct']}",
                f"- 当前处理：{card['status']}",
                f"- 证据等级：{card['evidence']}",
                '',
            ])
    else:
        lines.append('- 暂无卡片。')
        lines.append('')

    lines.extend(['## 三、检索明细', ''])
    if sections:
        for item, results in sections:
            lines.extend([
                f"### {item['theme']}",
                '',
                f"- 检索词组：{'；'.join(item['queries'])}",
                f"- 证据等级建议：{item['evidence']}",
                '',
            ])
            lines.extend(summarize_results(results))
            lines.append('')
    else:
        lines.append('- 当前未命中有效结果。')
        lines.append('')

    lines.extend([
        '## 四、写作提醒',
        '',
        '- 外部检索结果只能作为风险增强线索，优先级低于公告、交易所文件、法院文书和评级材料。',
        '- 如果命中港股退市、私有化、纾困链，后续必须优先回到 HKEXnews、SFC、公司通函和评级文件补 A 级证据。',
        '- 如果结果只来自媒体或市场讨论，正文只能写“公开报道显示”或“市场曾有一种解释”，不能写成公司已确认结论。',
    ])

    out_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(str(out_path))


if __name__ == '__main__':
    main()
