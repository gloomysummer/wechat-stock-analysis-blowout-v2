#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
巨潮资讯公告直连工具

能力：
1. 通过键盘精灵接口解析 A 股股票 orgId
2. 通过 hisAnnouncement/query 查询公告列表
3. 下载 PDF 并抽取文本
4. 解析业绩预告 / 业绩快报 / 年报摘要
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    requests = None

try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None


SOURCE_META = {
    'cninfo_annual_report_summary': ('formal_report', 70),
    'cninfo_annual_report_full': ('formal_report', 65),
    'cninfo_periodic_report': ('periodic_report', 60),
    'cninfo_performance_express': ('performance_express', 50),
    'cninfo_performance_forecast': ('performance_forecast', 40),
}


def safe_float(value):
    try:
        if value is None:
            return None
        result = float(value)
        return None if result != result else result
    except Exception:
        return None


def normalize_numeric_text(text):
    value = str(text or '').replace(',', '').replace('，', '').replace('\n', '').replace('\r', '').replace(' ', '').strip()
    if not value:
        return None
    try:
        return float(value)
    except Exception:
        return None


def amount_to_yuan(value, unit_hint=''):
    value = normalize_numeric_text(value)
    if value is None:
        return None
    unit_hint = str(unit_hint or '')
    if '亿' in unit_hint:
        return value * 100000000
    if '万' in unit_hint:
        return value * 10000
    return value


def amount_to_yuan_with_context(value, unit_hint='', context=''):
    parsed = amount_to_yuan(value, unit_hint)
    if parsed is None:
        return None
    if unit_hint:
        return parsed
    context = str(context or '')
    if '亿元' in context or '亿 元' in context or '亿' in context:
        return parsed * 100000000
    if '万元' in context or '万 元' in context or '万' in context:
        return parsed * 10000
    return parsed


def market_to_cninfo(ts_code: str):
    code = str(ts_code or '').upper()
    if code.endswith('.SZ'):
        return 'szse', 'sz'
    if code.endswith('.SH'):
        return 'sse', 'sh'
    return 'szse', 'sz'


def cninfo_headers():
    return {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search',
        'X-Requested-With': 'XMLHttpRequest',
    }


def extract_report_period(title: str, fallback_ts=None):
    title = str(title or '')
    m = re.search(r'(20\d{2})年度', title)
    if m:
        return f"{m.group(1)}1231"
    if fallback_ts:
        try:
            return f"{datetime.fromtimestamp(fallback_ts / 1000).year - 1}1231"
        except Exception:
            return ''
    return ''


def extract_report_period_from_text(text: str, title: str = '', fallback_ts=None):
    text = str(text or '')
    m = re.search(r'(20\d{2})年(?:度)?业绩预告', text)
    if m:
        return f"{m.group(1)}1231"
    m = re.search(r'(20\d{2})年(?:第)?三季度报告', text)
    if m:
        return f"{m.group(1)}0930"
    m = re.search(r'(20\d{2})年半年度报告', text)
    if m:
        return f"{m.group(1)}0630"
    m = re.search(r'(20\d{2})年(?:第)?一季度报告', text)
    if m:
        return f"{m.group(1)}0331"
    m = re.search(r'业绩预告期间[：:]\s*(20\d{2})年', text)
    if m:
        return f"{m.group(1)}1231"
    m = re.search(r'(20\d{2})年(?:度)?业绩快报', text)
    if m:
        return f"{m.group(1)}1231"
    m = re.search(r'(20\d{2})年年度报告摘要', text)
    if m:
        return f"{m.group(1)}1231"
    return extract_report_period(title, fallback_ts)


def extract_between(text: str, patterns):
    for pattern in patterns:
        m = re.search(pattern, text, re.S)
        if m:
            return m
    return None


def extract_metric_from_lines(text: str, keywords):
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    pattern = re.compile(r'([0-9,]+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)')
    for idx, line in enumerate(lines):
        window = ' '.join(lines[idx: idx + 3])
        for keyword in keywords:
            pos = window.find(keyword)
            if pos == -1:
                continue
            scoped = window[pos:]
            match = pattern.search(scoped)
            if match:
                return amount_to_yuan_with_context(match.group(1), match.group(2), match.group(0)), match.group(0)
    return None, ''


def extract_amount_tokens(text: str):
    pattern = re.compile(r'(-?[0-9][0-9,\n]*(?:\.\d+)?)\s*(亿元|亿|万元|万|元)?')
    values = []
    for match in pattern.finditer(str(text or '')):
        number = amount_to_yuan_with_context(match.group(1), match.group(2) or '', match.group(0))
        if number is not None:
            values.append((number, match.group(0)))
    return values


def extract_periodic_metric(text: str, label: str, prefer_ytd: bool = True):
    text = str(text or '')
    m = re.search(label, text, re.S)
    if not m:
        return None, ''
    block = text[m.start(): m.start() + 280]
    if prefer_ytd:
        percent_match = re.search(r'-?[0-9]+(?:\.\d+)?%', block)
        if percent_match:
            tail = block[percent_match.end():]
            amounts = extract_amount_tokens(tail)
            if amounts:
                return amounts[0]
    amounts = extract_amount_tokens(block)
    if amounts:
        return amounts[0]
    return None, ''


def extract_simple_number(text: str, label_patterns, max_chars=60):
    text = str(text or '')
    for label in label_patterns:
        m = re.search(label + rf'[\s\S]{{0,{max_chars}}}?(-?[0-9]+(?:\.\d+)?)', text)
        if m:
            try:
                return float(m.group(1)), m.group(0)
            except Exception:
                continue
    return None, ''


def fetch_cninfo_stock_identity(ts_code: str, name_hint: str = ''):
    if requests is None:
        return None
    sec_code = str(ts_code or '').split('.')[0]
    candidates = [sec_code]
    if name_hint:
        candidates.append(str(name_hint).strip())
    for keyword in candidates:
        if not keyword:
            continue
        try:
            resp = requests.post(
                'https://www.cninfo.com.cn/new/information/topSearch/query',
                data={'keyWord': keyword, 'maxNum': '10', 'plate': ''},
                headers=cninfo_headers(),
                timeout=20,
            )
            resp.raise_for_status()
            rows = resp.json()
        except Exception:
            continue
        for row in rows or []:
            if str(row.get('code') or '').strip() == sec_code:
                return {
                    'secCode': sec_code,
                    'orgId': str(row.get('orgId') or '').strip(),
                    'zwjc': str(row.get('zwjc') or name_hint or '').strip(),
                }
    return None


def fetch_cninfo_announcements(ts_code: str, name_hint: str = '', category: str = '', days_back: int = 540, searchkey: str = ''):
    identity = fetch_cninfo_stock_identity(ts_code, name_hint)
    if not identity or not identity.get('orgId') or requests is None:
        return []
    column, plate = market_to_cninfo(ts_code)
    start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    payload = {
        'pageNum': '1',
        'pageSize': '30',
        'column': column,
        'tabName': 'fulltext',
        'plate': plate,
        'stock': f"{identity['secCode']},{identity['orgId']}",
        'searchkey': searchkey,
        'secid': '',
        'category': category,
        'trade': '',
        'seDate': f'{start_date}~{end_date}',
        'sortName': 'time',
        'sortType': 'desc',
        'isHLtitle': 'true',
    }
    try:
        resp = requests.post(
            'https://www.cninfo.com.cn/new/hisAnnouncement/query',
            data=payload,
            headers=cninfo_headers(),
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('announcements') or []
    except Exception:
        return []


def download_cninfo_pdf_text(adjunct_url: str):
    if not adjunct_url or requests is None or PdfReader is None:
        return ''
    url = adjunct_url if adjunct_url.startswith('http') else 'https://static.cninfo.com.cn/' + adjunct_url.lstrip('/')
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.cninfo.com.cn/'}, timeout=30)
        resp.raise_for_status()
        reader = PdfReader(io.BytesIO(resp.content))
        pages = []
        for page in reader.pages[:12]:
            pages.append(page.extract_text() or '')
        text = '\n'.join(pages)
        # 修正常见的 PDF 抽取断裂：中文词被断行、数字被断行
        text = re.sub(r'(?<=[\u4e00-\u9fff])\s*\n\s*(?=[\u4e00-\u9fff])', '', text)
        text = re.sub(r'(?<=\d)\s*\n\s*(?=\d)', '', text)
        return text
    except Exception:
        return ''


def build_cninfo_detail_url(announcement: dict):
    ts = announcement.get('announcementTime') or 0
    date_text = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d') if ts else ''
    return 'https://www.cninfo.com.cn/new/disclosure/detail?stockCode={}&announcementId={}&orgId={}&announcementTime={}'.format(
        announcement.get('secCode') or '',
        announcement.get('announcementId') or '',
        announcement.get('orgId') or '',
        date_text,
    )




def normalize_forecast_range_token(raw):
    raw = str(raw or '').strip()
    if not raw:
        return raw
    # PDF 文本里常把“区间上限”和“上年同期值”粘成一个串，例如 -6,0003,362.19
    import re
    m = re.match(r'^(-?\d{1,3}(?:,\d{3})+)(\d[\d,]*(?:\.\d+)?)$', raw)
    if m:
        return m.group(1)
    return raw

def attach_source_meta(item: dict):
    source_type = str(item.get('source_type') or '')
    source_level, source_priority = SOURCE_META.get(source_type, ('unknown', 0))
    item['source_level'] = source_level
    item['source_priority'] = source_priority
    return item


def parse_cninfo_forecast_announcement(announcement: dict):
    title = str(announcement.get('announcementTitle') or '')
    text = download_cninfo_pdf_text(announcement.get('adjunctUrl') or '')
    if not text:
        return None
    net_match = extract_between(
        text,
        [
            r'归属于上市公司股(?:东)?的净利润\s*(-?[0-9,]+(?:\.\d+)?)\s*[~～]\s*(-?[0-9,]+(?:\.\d+)?)',
            r'净利润\s*(-?[0-9,]+(?:\.\d+)?)\s*[~～]\s*(-?[0-9,]+(?:\.\d+)?)',
        ],
    )
    if not net_match:
        return None
    yoy_match = extract_between(
        text,
        [
            r'增长\s*([0-9.]+)%\s*[~～\-至]\s*([0-9.]+)%',
            r'变动幅度\s*([0-9.]+)%\s*[~～\-至]\s*([0-9.]+)%',
        ],
    )
    reason_match = extract_between(text, [r'三、业绩变动原因说明\s*(.*?)\s*四、其他相关说明'])
    summary = re.sub(r'\s+', ' ', reason_match.group(1)).strip() if reason_match else title
    min_profit = normalize_numeric_text(normalize_forecast_range_token(net_match.group(1)))
    max_profit = normalize_numeric_text(normalize_forecast_range_token(net_match.group(2)))
    if min_profit is None or max_profit is None:
        return None
    min_profit *= 10000
    max_profit *= 10000
    yoy_min = normalize_numeric_text(yoy_match.group(1)) if yoy_match else None
    yoy_max = normalize_numeric_text(yoy_match.group(2)) if yoy_match else None
    return attach_source_meta({
        'source_type': 'cninfo_performance_forecast',
        'source_title': title or '巨潮业绩预告',
        'report_period': extract_report_period_from_text(text, title, announcement.get('announcementTime')),
        'disclosure_date': datetime.fromtimestamp((announcement.get('announcementTime') or 0) / 1000).strftime('%Y%m%d') if announcement.get('announcementTime') else '',
        'is_estimate': True,
        'source_url': build_cninfo_detail_url(announcement),
        'metrics': {
            'net_profit': {
                'min': min_profit,
                'max': max_profit,
                'unit': '元',
                'yoy_min': yoy_min,
                'yoy_max': yoy_max,
            },
        },
        'summary': summary,
        'raw_excerpt': text[:1200],
    })


def parse_cninfo_express_announcement(announcement: dict):
    title = str(announcement.get('announcementTitle') or '')
    text = download_cninfo_pdf_text(announcement.get('adjunctUrl') or '')
    if not text:
        return None
    revenue_match = extract_between(text, [r'营业总收入\s*([0-9,]+(?:\.\d+)?)\s*(亿|万元|万|元)?', r'营业收入\s*([0-9,]+(?:\.\d+)?)\s*(亿|万元|万|元)?'])
    profit_match = extract_between(text, [r'归属于上市公司股东的净利润\s*([0-9,]+(?:\.\d+)?)\s*(亿|万元|万|元)?'])
    yoy_rev_match = extract_between(text, [r'营业总收入.*?增减变动幅度\s*([0-9.\-]+)%', r'营业收入.*?增减变动幅度\s*([0-9.\-]+)%'])
    yoy_profit_match = extract_between(text, [r'归属于上市公司股东的净利润.*?增减变动幅度\s*([0-9.\-]+)%'])
    if not revenue_match or not profit_match:
        return None
    revenue = amount_to_yuan(revenue_match.group(1), revenue_match.group(2) if revenue_match.lastindex and revenue_match.lastindex >= 2 else '')
    profit = amount_to_yuan(profit_match.group(1), profit_match.group(2) if profit_match.lastindex and profit_match.lastindex >= 2 else '')
    if revenue is None or profit is None:
        return None
    return attach_source_meta({
        'source_type': 'cninfo_performance_express',
        'source_title': title or '巨潮业绩快报',
        'report_period': extract_report_period_from_text(text, title, announcement.get('announcementTime')),
        'disclosure_date': datetime.fromtimestamp((announcement.get('announcementTime') or 0) / 1000).strftime('%Y%m%d') if announcement.get('announcementTime') else '',
        'is_estimate': False,
        'source_url': build_cninfo_detail_url(announcement),
        'metrics': {
            'revenue': {'value': revenue, 'unit': '元', 'yoy': normalize_numeric_text(yoy_rev_match.group(1)) if yoy_rev_match else None},
            'net_profit': {'value': profit, 'unit': '元', 'yoy': normalize_numeric_text(yoy_profit_match.group(1)) if yoy_profit_match else None},
        },
        'summary': title,
        'raw_excerpt': text[:1200],
    })


def parse_cninfo_annual_summary_announcement(announcement: dict):
    title = str(announcement.get('announcementTitle') or '')
    text = download_cninfo_pdf_text(announcement.get('adjunctUrl') or '')
    if not text:
        return None
    revenue = None
    profit = None
    revenue_raw = ''
    profit_raw = ''

    revenue_match = extract_between(
        text,
        [
            r'营业[\s\S]{0,6}?总?[\s\S]{0,6}?收[\s\S]{0,6}?入[^0-9]{0,20}([0-9,]+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)',
        ],
    )
    if revenue_match:
        revenue = amount_to_yuan_with_context(revenue_match.group(1), revenue_match.group(2), revenue_match.group(0))
        revenue_raw = revenue_match.group(0)
    else:
        revenue, revenue_raw = extract_metric_from_lines(text, ['营业收入', '营业总收入'])

    profit_match = extract_between(
        text,
        [
            r'归属于上市公司股东的净利润[^0-9]{0,20}([0-9,]+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)',
        ],
    )
    if profit_match:
        profit = amount_to_yuan_with_context(profit_match.group(1), profit_match.group(2), profit_match.group(0))
        profit_raw = profit_match.group(0)
    else:
        profit, profit_raw = extract_metric_from_lines(text, ['归属于上市公司股东的净利润'])

    if revenue is None or profit is None:
        return None
    return attach_source_meta({
        'source_type': 'cninfo_annual_report_summary' if '摘要' in title else 'cninfo_annual_report_full',
        'source_title': title or '巨潮年度报告摘要',
        'report_period': extract_report_period_from_text(text, title, announcement.get('announcementTime')),
        'disclosure_date': datetime.fromtimestamp((announcement.get('announcementTime') or 0) / 1000).strftime('%Y%m%d') if announcement.get('announcementTime') else '',
        'is_estimate': False,
        'source_url': build_cninfo_detail_url(announcement),
        'metrics': {
            'revenue': {'value': revenue, 'unit': '元'},
            'net_profit': {'value': profit, 'unit': '元'},
        },
        'summary': title,
        'raw_excerpt': (revenue_raw + '\n' + profit_raw + '\n' + text[:1000]).strip(),
    })


def parse_cninfo_periodic_report_announcement(announcement: dict):
    title = str(announcement.get('announcementTitle') or '')
    text = download_cninfo_pdf_text(announcement.get('adjunctUrl') or '')
    if not text:
        return None

    report_period = extract_report_period_from_text(text, title, announcement.get('announcementTime'))
    prefer_ytd = '三季度' in title or '一季度' in title

    revenue, revenue_raw = extract_periodic_metric(text, r'营业收入', prefer_ytd=prefer_ytd)
    net_profit, net_profit_raw = extract_periodic_metric(text, r'归属于上市公司股东的净利润', prefer_ytd=prefer_ytd)
    deduced_profit, deduced_raw = extract_periodic_metric(text, r'归属于上市公司股东的扣除非经常性损益的净利润', prefer_ytd=prefer_ytd)
    ocf, ocf_raw = extract_periodic_metric(text, r'经营活动产生的现金流量净额', prefer_ytd=False)
    total_assets, assets_raw = extract_periodic_metric(text, r'总资产', prefer_ytd=False)
    net_assets, equity_raw = extract_periodic_metric(text, r'归属于上市公司股东的所有者权益', prefer_ytd=False)
    eps, eps_raw = extract_simple_number(text, [r'基本每股收益', r'稀释每股收益'], max_chars=40)

    if revenue is None and net_profit is None:
        return None

    metrics = {}
    if revenue is not None:
        metrics['revenue'] = {'value': revenue, 'unit': '元'}
    if net_profit is not None:
        metrics['net_profit'] = {'value': net_profit, 'unit': '元'}
    if deduced_profit is not None:
        metrics['deducted_net_profit'] = {'value': deduced_profit, 'unit': '元'}
    if ocf is not None:
        metrics['operating_cashflow'] = {'value': ocf, 'unit': '元'}
    if total_assets is not None:
        metrics['total_assets'] = {'value': total_assets, 'unit': '元'}
    if net_assets is not None:
        metrics['equity'] = {'value': net_assets, 'unit': '元'}
    if eps is not None:
        metrics['eps'] = {'value': eps, 'unit': '元/股'}

    excerpt = '\n'.join(x for x in [revenue_raw, net_profit_raw, deduced_raw, ocf_raw, assets_raw, equity_raw, eps_raw] if x)
    return attach_source_meta({
        'source_type': 'cninfo_periodic_report',
        'source_title': title or '巨潮定期报告',
        'report_period': report_period,
        'disclosure_date': datetime.fromtimestamp((announcement.get('announcementTime') or 0) / 1000).strftime('%Y%m%d') if announcement.get('announcementTime') else '',
        'is_estimate': False,
        'source_url': build_cninfo_detail_url(announcement),
        'metrics': metrics,
        'summary': title,
        'raw_excerpt': (excerpt + '\n' + text[:1000]).strip(),
    })


def fetch_cninfo_latest_disclosure(ts_code: str, name_hint: str = ''):
    announcements = []
    announcements.extend(fetch_cninfo_announcements(ts_code, name_hint, category='category_yjygjxz_szsh', days_back=540))
    announcements.extend(fetch_cninfo_announcements(ts_code, name_hint, category='category_ndbg_szsh', days_back=540))
    announcements.extend(fetch_cninfo_announcements(ts_code, name_hint, category='category_bndbg_szsh', days_back=540))
    announcements.extend(fetch_cninfo_announcements(ts_code, name_hint, category='category_sjdbg_szsh', days_back=540))
    announcements.extend(fetch_cninfo_announcements(ts_code, name_hint, category='category_yjdbg_szsh', days_back=540))
    announcements.extend(fetch_cninfo_announcements(ts_code, name_hint, category='', days_back=540, searchkey='业绩快报'))

    parsed = []
    seen = set()
    for ann in announcements:
        ann_id = ann.get('announcementId')
        if ann_id in seen:
            continue
        seen.add(ann_id)
        title = str(ann.get('announcementTitle') or '')
        item = None
        if '业绩预告' in title:
            item = parse_cninfo_forecast_announcement(ann)
        elif '业绩快报' in title:
            item = parse_cninfo_express_announcement(ann)
        elif '半年度报告' in title or '三季度报告' in title or '一季度报告' in title:
            item = parse_cninfo_periodic_report_announcement(ann)
        elif '年度报告摘要' in title or title.endswith('年度报告'):
            item = parse_cninfo_annual_summary_announcement(ann)
        if item:
            parsed.append(item)

    if not parsed:
        return None

    def sort_key(item):
        report_period = item.get('report_period') or ''
        disclosure_date = item.get('disclosure_date') or ''
        source_type = str(item.get('source_type') or '')
        source_rank = SOURCE_META.get(source_type, ('unknown', 0))[1]
        return (report_period, disclosure_date, source_rank)

    return sorted(parsed, key=sort_key, reverse=True)[0]
