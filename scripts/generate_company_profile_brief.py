#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成公司概况底稿，作为无财务结构化数据时的写稿补充上下文。

用法：
  python3 scripts/generate_company_profile_brief.py <company_name> <company_profile_brief.md>
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/lib')
from tavily_pool import get_routed_key, mark_key_error, mark_key_success


OFFICIAL_DOMAIN_BONUS = {
    'hkexnews.hk': 6,
    'hkex.com.hk': 5,
    'cninfo.com.cn': 5,
    'static.cninfo.com.cn': 5,
    'sse.com.cn': 5,
    'szse.cn': 5,
    'csrc.gov.cn': 5,
}
MEDIA_DOMAIN_BONUS = {
    'finance.sina.com.cn': 3,
    'eastmoney.com': 3,
    'cls.cn': 3,
    'stcn.com': 3,
    'ifeng.com': 2,
    '21jingji.com': 3,
    'gelonghui.com': 2,
    'finet.hk': 2,
}
EXCLUDE = ['招聘', '脉脉', 'BOSS直聘', '猎聘', '百科', '公司章程']


def normalize(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def tavily_search(query, max_results=8, days=365*8):
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
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    try:
        resp = requests.post('https://api.tavily.com/search', headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            mark_key_success(key)
            return resp.json().get('results', []) or []
        mark_key_error(key, f'HTTP {resp.status_code}')
        return []
    except Exception as e:
        mark_key_error(key, str(e))
        return []


def domain_kind(url):
    d = urlparse(str(url or '')).netloc.lower()
    for host in OFFICIAL_DOMAIN_BONUS:
        if host in d:
            return 'official'
    for host in MEDIA_DOMAIN_BONUS:
        if host in d:
            return 'media'
    return 'other'


def extract_related_entities(items, company):
    seeds = [
        '山东三星集团',
        '三星集团',
        '邹平三星',
        '邹平三星油脂工业有限公司',
        '中国信达',
        '中国信达资产管理股份有限公司',
        '中国信达资产管理股份有限公司山东分公司',
        '华融',
        '东方资产',
        '长城资产',
    ]
    found = []
    blob = ' '.join((str(item.get('title') or '') + ' ' + str(item.get('content') or '')) for item in items)

    def add(name):
        name = str(name).strip()
        if not name or name == company or name in found:
            return
        found.append(name)

    for seed in seeds:
        if seed in blob and seed != company:
            add(seed)

    import re
    boundary_patterns = [
        r'(?<![一-龥A-Za-z])(山东三星集团)(?![一-龥A-Za-z])',
        r'(?<![一-龥A-Za-z])(三星集团)(?![一-龥A-Za-z])',
        r'(?<![一-龥A-Za-z])(邹平三星油脂工业有限公司)(?![一-龥A-Za-z])',
        r'(?<![一-龥A-Za-z])(中国信达资产管理股份有限公司山东分公司)(?![一-龥A-Za-z])',
        r'(?<![一-龥A-Za-z])(中国信达资产管理股份有限公司)(?![一-龥A-Za-z])',
    ]
    for pat in boundary_patterns:
        for match in re.findall(pat, blob):
            add(match)

    return found[:8]


def relevant(results, company):
    out = []
    seen = set()
    for r in results:
        url = str(r.get('url') or '')
        title = normalize(r.get('title', ''))
        content = normalize(r.get('content', ''))
        text = f"{title} {content} {url}"
        if url in seen:
            continue
        if any(k in text for k in EXCLUDE):
            continue
        if company not in text:
            continue
        seen.add(url)
        out.append({
            'title': title,
            'url': url,
            'content': content[:240],
            'kind': domain_kind(url),
        })
    return out[:5]


def search_bundle(company):
    bundles = {
        'basic': [
            f'{company} 公司简介 主营业务 上市代码 上市地位',
            f'{company} listing business profile',
        ],
        'listing': [
            f'{company} 退市 私有化 协议安排 联合公告',
            f'{company} delisting privatization scheme of arrangement',
        ],
        'risk': [
            f'{company} 债务展期 违约 评级下调 中国信达 华融',
            f'{company} debt restructuring default rating downgrade',
        ],
    }
    output = {}
    for key, queries in bundles.items():
        merged = []
        for query in queries:
            merged.extend(relevant(tavily_search(query), company))
        uniq = []
        used = set()
        for item in merged:
            if item['url'] in used:
                continue
            used.add(item['url'])
            uniq.append(item)
        output[key] = uniq[:5]
    return output




def build_fallback_profile(financial_data_path, company):
    import json
    try:
        data = json.loads(Path(financial_data_path).read_text(encoding='utf-8'))
    except Exception:
        return {}
    stock_basic_rows = data.get('stock_basic') or []
    stock_basic = stock_basic_rows[0] if stock_basic_rows else {}
    latest = data.get('latest_disclosure') or {}
    return {
        'company': company,
        'ts_code': str(stock_basic.get('ts_code') or '').strip(),
        'name': str(stock_basic.get('name') or company).strip(),
        'list_date': str(stock_basic.get('list_date') or '').strip(),
        'market': str(stock_basic.get('market') or '').strip(),
        'area': str(stock_basic.get('area') or '').strip(),
        'industry': str(stock_basic.get('industry') or '').strip(),
        'latest_title': str(latest.get('source_title') or '').strip(),
        'latest_period': str(latest.get('report_period') or '').strip(),
        'latest_date': str(latest.get('disclosure_date') or '').strip(),
        'latest_url': str(latest.get('source_url') or '').strip(),
    }

def main():
    if len(sys.argv) < 3:
        raise SystemExit('usage: generate_company_profile_brief.py <company_name> <output_md> [financial_data.json]')
    company = sys.argv[1].strip()
    out = Path(sys.argv[2])
    financial_data_path = sys.argv[3] if len(sys.argv) > 3 else ''
    bundles = search_bundle(company)
    fallback = build_fallback_profile(financial_data_path, company) if financial_data_path else {}

    lines = [
        f'# 公司概况底稿：{company}',
        '',
        f'- 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}',
        '- 说明：当没有结构化财务数据时，用于给写稿模型补充公司基础信息、上市地位、历史交易结构与公开风险背景。',
        '',
        '## 一、基础信息线索',
        '',
    ]
    if bundles['basic']:
        for item in bundles['basic']:
            lines.append(f"- {item['title']}\\n  来源：{item['url']}\\n  摘要：{item['content']}")
    else:
        if fallback:
            lines.append(f"- 结构化底稿显示：证券代码 {fallback.get('ts_code') or '未识别'}，证券简称 {fallback.get('name') or company}，所属行业 {fallback.get('industry') or '未识别'}，地区 {fallback.get('area') or '未识别'}。")
            if fallback.get('list_date'):
                lines.append(f"- 上市时间：{fallback.get('list_date')} ｜ 市场层级：{fallback.get('market') or '未识别'}")
        else:
            lines.append('- 暂未命中足够清晰的基础信息线索。')

    lines.extend(['', '## 二、上市状态与历史交易线索', ''])
    if bundles['listing']:
        for item in bundles['listing']:
            lines.append(f"- {item['title']}\\n  来源：{item['url']}\\n  摘要：{item['content']}")
    else:
        if fallback and (fallback.get('latest_title') or fallback.get('ts_code')):
            lines.append(f"- 当前可确认上市身份：{fallback.get('name') or company}（{fallback.get('ts_code') or '未识别代码'}），暂无外部检索命中的退市/私有化链条。")
            if fallback.get('latest_title'):
                lines.append(f"- 最新披露：{fallback.get('latest_title')}（期别 {fallback.get('latest_period') or '未注明'}，披露日 {fallback.get('latest_date') or '未注明'}）")
                if fallback.get('latest_url'):
                    lines.append(f"  来源：{fallback.get('latest_url')}")
        else:
            lines.append('- 暂未命中足够清晰的上市/退市/私有化线索。')

    lines.extend(['', '## 三、债务/纾困/信用线索', ''])
    if bundles['risk']:
        for item in bundles['risk']:
            lines.append(f"- {item['title']}\\n  来源：{item['url']}\\n  摘要：{item['content']}")
    else:
        if fallback and fallback.get('latest_title'):
            lines.append('- Tavily 未命中债务/纾困公开网页线索；建议后续优先结合最新公告、审计意见和问询函继续补链。')
        else:
            lines.append('- 暂未命中足够清晰的债务/纾困/信用线索。')

    related = extract_related_entities([item for items in bundles.values() for item in items], company)
    lines.extend(['', '## 四、潜在关联主体', ''])
    if related:
        lines.extend([f'- {name}' for name in related])
    else:
        lines.append('- 暂未自动识别到高置信关联主体。')

    lines.extend(['', '## 五、写作提醒', ''])
    lines.extend([
        '- 这份底稿只提供公开线索，不等于官方最终结论。',
        '- 如果涉及港股退市、私有化、纾困，后续仍应优先回到港交所公告、SFC、公司通函和评级材料核实。',
        '- 正文里可以引用这些线索解释公司背景，但要区分官方事实、公开报道和市场解释。',
    ])
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(str(out))


if __name__ == '__main__':
    main()
