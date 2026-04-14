#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
企业深度财务分析脚本 v3.0
集成 enterprise-risk-analysis 能力 + 爆款风格输出 + 最新季报跟踪

功能：
1. Tushare 7 接口数据获取（近3年）
2. 3年趋势分析 + 资产/费用结构分析
3. 利润质量检验 + 杜邦分解
4. 自动风险预警扫描
5. 爆款风格叙事输出
6. ⚡ 最新季报跟踪（自动检测并对比同期数据）

用法：python3 scripts/financial_analysis.py <股票代码>
"""

import os, sys, json, re
from datetime import datetime, timedelta

try:
    import tushare as ts
    import pandas as pd
except ImportError:
    print("❌ 请先安装: pip install tushare pandas")
    sys.exit(1)

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
WORKSPACE_ROOT = os.path.abspath(os.path.join(SKILL_ROOT, '..', '..'))

for extra_path in (SCRIPT_DIR, SKILL_ROOT, WORKSPACE_ROOT):
    if extra_path not in sys.path:
        sys.path.insert(0, extra_path)

try:
    from cninfo_disclosure import fetch_cninfo_latest_disclosure as fetch_cninfo_latest_disclosure_external
except ImportError:
    fetch_cninfo_latest_disclosure_external = None


# ─── 配置 ─────────────────────────────────────────────────────────────────────

def get_token():
    token = os.environ.get('TUSHARE_TOKEN', '')
    if not token:
        env_paths = [
            os.path.join(os.path.dirname(__file__), '..', '.env'),
            os.path.join(os.getcwd(), '.env'),
        ]
        for p in env_paths:
            if os.path.exists(p):
                for line in open(p):
                    line = line.strip()
                    if line.startswith('TUSHARE_TOKEN='):
                        token = line.split('=', 1)[1].strip().strip('"').strip("'")
                        break
                if token:
                    break
    if not token:
        print("❌ 缺少 TUSHARE_TOKEN")
        sys.exit(1)
    return token


# ─── 工具函数 ──────────────────────────────────────────────────────────────────

def sv(df, col, idx=0, default=None):
    """安全取值"""
    if df is None or df.empty or col not in df.columns:
        return default
    try:
        v = df.iloc[idx][col]
        return default if (v != v) else v  # NaN check
    except (IndexError, KeyError):
        return default

def fmt(v, unit='', d=2):
    if v is None: return '—'
    if abs(v) >= 1e8: return f"{v/1e8:.{d}f}亿{unit}"
    if abs(v) >= 1e4: return f"{v/1e4:.{d}f}万{unit}"
    return f"{v:.{d}f}{unit}"

def pct(v):
    return f"{v:.2f}%" if v is not None else '—'

def arrow(v):
    if v is None: return ''
    return ' ↗' if v > 0.5 else (' ↘' if v < -0.5 else ' →')

def year_label(end_date):
    """从 end_date 提取年份标签"""
    if end_date is None: return '?'
    s = str(end_date)
    if len(s) >= 4: return s[:4]
    return '?'


def safe_float(value):
    try:
        if value is None:
            return None
        result = float(value)
        return None if result != result else result
    except Exception:
        return None


def to_jsonable_payload(value):
    if value is None:
        return []
    if hasattr(value, 'to_dict') and hasattr(value, 'empty'):
        return value.to_dict(orient='records') if not value.empty else []
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)

def fetch_cninfo_latest_disclosure(ts_code: str, name_hint: str = ''):
    if fetch_cninfo_latest_disclosure_external is None:
        return None
    return fetch_cninfo_latest_disclosure_external(ts_code, name_hint)


# ─── 数据获取 ──────────────────────────────────────────────────────────────────

def fetch_data(pro, ts_code):
    end_d = datetime.now().strftime('%Y%m%d')
    start_d = (datetime.now() - timedelta(days=365*4)).strftime('%Y%m%d')  # 多取1年保险
    print(f"📊 获取 {ts_code} 财务数据 ({start_d} ~ {end_d})")
    data = {}
    apis = [
        ('fina_indicator', lambda: pro.fina_indicator(
            ts_code=ts_code, limit=20,
            fields='ts_code,ann_date,end_date,roe,roe_dt,netprofit_margin,'
                   'grossprofit_margin,current_ratio,quick_ratio,debt_to_assets,'
                   'or_yoy,op_yoy,netprofit_yoy,tr_yoy,assets_turn,inv_turn,'
                   'ar_turn,ocf_to_profit,equity_yoy')),
        ('balancesheet', lambda: pro.balancesheet(
            ts_code=ts_code, start_date=start_d, end_date=end_d,
            fields='ts_code,ann_date,end_date,total_assets,total_liab,'
                   'total_hldr_eqy_inc_min_int,total_cur_assets,total_cur_liab,'
                   'money_cap,inventories,accounts_receiv,total_nca,notes_receiv,'
                   'lt_borr,bond_payable,goodwill,oth_eqt_tools_p_shr')),
        ('income', lambda: pro.income(
            ts_code=ts_code, start_date=start_d, end_date=end_d,
            fields='ts_code,ann_date,end_date,total_revenue,revenue,oper_cost,total_cogs,'
                   'operate_profit,total_profit,n_income,sell_exp,admin_exp,rd_exp,fin_exp')),
        ('cashflow', lambda: pro.cashflow(
            ts_code=ts_code, start_date=start_d, end_date=end_d,
            fields='ts_code,ann_date,end_date,n_cashflow_act,n_cashflow_inv_act,'
                   'n_cash_flows_fnc_act,free_cashflow,c_fr_sale_sg,c_pay_acq_const_fiolta')),
        ('fina_audit', lambda: pro.fina_audit(ts_code=ts_code, limit=5)),
        ('holdertrade', lambda: pro.stk_holdertrade(ts_code=ts_code, limit=20)),
        ('stock_basic', lambda: pro.stock_basic(
            ts_code=ts_code, fields='ts_code,name,list_date,market,area,industry,cnspell')),
        ('fina_mainbz', lambda: pro.fina_mainbz(ts_code=ts_code, type='P')),
        ('express', lambda: pro.express(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=10)),
        ('forecast', lambda: pro.forecast(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=10)),
    ]
    total = len(apis)
    for i, (name, fn) in enumerate(apis, 1):
        print(f"  [{i}/{total}] {name}...")
        try:
            data[name] = fn()
        except Exception as e:
            print(f"    ⚠️ {e}")
            data[name] = None
    print("  ✅ 完成\n")

    # v2: 全局去重 - 清理 Tushare 返回的重复行
    for name in list(data.keys()):
        df = data[name]
        if df is not None and not df.empty:
            before = len(df)
            if 'end_date' in df.columns:
                if name != 'fina_mainbz':
                    df = df.drop_duplicates(subset=['end_date'], keep='first')
                else:
                    df = df.drop_duplicates(subset=['end_date', 'bz_item'], keep='first')
            else:
                df = df.drop_duplicates(keep='first')
            data[name] = df
            dropped = before - len(df)
            if dropped > 0:
                print(f"    🗑 {name}: 去除 {dropped} 条重复数据")

    return data


# ─── AKShare 港股财务数据 ─────────────────────────────────────────────────────

def is_hk_code(ts_code):
    """判断是否为港股代码（如 09992.HK）"""
    return bool(re.match(r'^\d{4,6}\.HK$', str(ts_code), re.IGNORECASE))

def fetch_hk_data_akshare(hk_code):
    """使用 AKShare 获取港股财务三大表数据，返回与 Tushare fetch_data 兼容的 data dict"""
    if not HAS_AKSHARE:
        print("  ⚠️ AKShare 未安装，跳过港股数据获取")
        return None

    # 清理代码格式（去掉 .HK 后缀，保留前导零）
    raw_code = str(hk_code).upper().replace('.HK', '')
    display_code = raw_code

    print(f"📊 使用 AKShare 获取港股 {display_code} 财务数据")
    data = {}

    # 1. 财务指标分析
    print(f"  [1/6] stock_financial_hk_analysis_indicator_em...")
    try:
        df = ak.stock_financial_hk_analysis_indicator_em(symbol=raw_code)
        if df is not None and not df.empty:
            print(f"    ✅ 财务指标: {len(df)} 条")
            data['fina_indicator'] = df
        else:
            data['fina_indicator'] = None
    except Exception as e:
        print(f"    ⚠️ {e}")
        data['fina_indicator'] = None

    # 2. 港股财务报表
    print(f"  [2/6] stock_financial_hk_report_em...")
    try:
        df = ak.stock_financial_hk_report_em(symbol=raw_code)
        if df is not None and not df.empty:
            print(f"    ✅ 财务报表: {len(df)} 条")
            data['hk_report'] = df
        else:
            data['hk_report'] = None
    except Exception as e:
        print(f"    ⚠️ {e}")
        data['hk_report'] = None

    # 3. 港股财务指标
    print(f"  [3/6] stock_hk_financial_indicator_em...")
    try:
        df = ak.stock_hk_financial_indicator_em(symbol=raw_code)
        if df is not None and not df.empty:
            print(f"    ✅ 财务指标快照: {len(df)} 条")
            data['hk_financial_indicator'] = df
        else:
            data['hk_financial_indicator'] = None
    except Exception as e:
        print(f"    ⚠️ {e}")
        data['hk_financial_indicator'] = None

    # 4. 港股分红数据
    print(f"  [4/6] stock_hk_dividend_payout_em...")
    try:
        df = ak.stock_hk_dividend_payout_em(symbol=raw_code)
        if df is not None and not df.empty:
            print(f"    ✅ 分红数据: {len(df)} 条")
            data['hk_dividend'] = df
        else:
            data['hk_dividend'] = None
    except Exception as e:
        print(f"    ⚠️ {e}")
        data['hk_dividend'] = None

    # 5. 港股公司简介
    print(f"  [5/6] stock_hk_company_profile_em...")
    try:
        df = ak.stock_hk_company_profile_em(symbol=raw_code)
        if df is not None and not df.empty:
            print(f"    ✅ 公司简介: {len(df)} 条")
            data['stock_basic'] = df
        else:
            data['stock_basic'] = None
    except Exception as e:
        print(f"    ⚠️ {e}")
        data['stock_basic'] = None

    # 6. 用 Tavily 搜索补充三大表数据
    print(f"  [6/6] Tavily 搜索三大表数据...")
    try:
        from lib.tavily_pool import get_routed_key
        import requests

        key = get_routed_key(route='china_hk_finance')
        if key:
            url = 'https://api.tavily.com/search'
            payload = {
                'api_key': key,
                'query': f'{display_code} {display_code.replace(".HK","")} 2023 2024 2025 年报 三大财务报表 营业收入 净利润 总资产 总负债 现金流 具体数字',
                'search_depth': 'advanced',
                'include_answer': True,
                'max_results': 10
            }
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                tavily_data = resp.json()
                if tavily_data.get('answer'):
                    data['hk_tavily_answer'] = tavily_data['answer']
                    print(f"    ✅ AI回答: {tavily_data['answer'][:100]}...")
                if tavily_data.get('results'):
                    data['hk_tavily_results'] = tavily_data['results'][:5]
                    print(f"    ✅ 搜索结果: {len(tavily_data['results'])} 条")
            else:
                print(f"    ⚠️ Tavily 请求失败: {resp.status_code}")
        else:
            print(f"    ⚠️ 无可用 Tavily key")
    except Exception as e:
        print(f"    ⚠️ Tavily 搜索失败: {e}")

    print("  ✅ AKShare 港股数据获取完成\n")
    return data


def adapt_hk_data_to_tushare(data):
    """将 AKShare 港股数据转换为 Tushare 兼容格式，使现有分析函数可以直接使用"""
    fi_df = data.get('fina_indicator')
    if fi_df is None or fi_df.empty:
        return data

    print("  🔄 转换 AKShare 列名为 Tushare 兼容格式...")

    # AKShare → Tushare 列名映射
    col_map = {
        'ROE_AVG': 'roe',

        'GROSS_PROFIT_RATIO': 'grossprofit_margin',
        'NET_PROFIT_RATIO': 'netprofit_margin',
        'DEBT_ASSET_RATIO': 'debt_to_assets',
        'CURRENT_RATIO': 'current_ratio',
        'OPERATE_INCOME_YOY': 'tr_yoy',
        'HOLDER_PROFIT_YOY': 'netprofit_yoy',
        'OPERATE_INCOME': 'revenue',
        'HOLDER_PROFIT': 'n_income',
        'GROSS_PROFIT': 'gross_profit',
        'PER_NETCASH_OPERATE': 'ocf_to_profit',
        'PER_OI': 'eps',
        'BASIC_EPS': 'basic_eps',
        'DILUTED_EPS': 'diluted_eps',
        'BPS': 'bps',
        'ROA': 'roa',
        'ROIC_YEARLY': 'roic',
        'TAX_EBT': 'tax_earnings',
        'OCF_SALES': 'ocf_to_revenue',
        'CURRENTDEBT_DEBT': 'current_debt_ratio',
        'OPERATE_INCOME_QOQ': 'revenue_qoq',
        'GROSS_PROFIT_QOQ': 'gross_profit_qoq',
        'HOLDER_PROFIT_QOQ': 'net_profit_qoq',
    }

    df = fi_df.rename(columns=col_map)

    # 添加 end_date（从 REPORT_DATE 提取）
    if 'REPORT_DATE' in df.columns:
        df['end_date'] = pd.to_datetime(df['REPORT_DATE']).dt.strftime('%Y%m%d')
    if 'FISCAL_YEAR' in df.columns:
        df['ann_date'] = df['FISCAL_YEAR'].apply(lambda x: f'{x}-12-31'.replace('-', '') if isinstance(x, str) and len(x) >= 4 else None)

    # 添加 ts_code
    if 'SECURITY_CODE' in df.columns:
        df['ts_code'] = df['SECURITY_CODE'].apply(lambda x: f'{str(x).zfill(5)}.HK')
    elif 'SECUCODE' in df.columns:
        df['ts_code'] = df['SECUCODE']

    # 添加公司名称
    if 'SECURITY_NAME_ABBR' in df.columns:
        df['name'] = df['SECURITY_NAME_ABBR']

    # 按 end_date 降序排序（最新的在前）
    if 'end_date' in df.columns:
        df = df.sort_values('end_date', ascending=False).reset_index(drop=True)

    # 替换回 fina_indicator
    data['fina_indicator'] = df
    print(f"    ✅ 已转换 {len(df)} 条财务指标数据")

    # 为 stock_basic 添加 Tushare 兼容字段
    basic_df = data.get('stock_basic')
    if basic_df is not None and not basic_df.empty:
        rename_map = {}
        if '公司名称' in basic_df.columns:
            rename_map['公司名称'] = 'name'
        if '所属行业' in basic_df.columns:
            rename_map['所属行业'] = 'industry'
        if rename_map:
            basic_df = basic_df.rename(columns=rename_map)
            data['stock_basic'] = basic_df
            print(f"    ✅ 公司简介字段已转换")

    return data


def derive_latest_disclosure(data):
    """优先提取最新业绩披露（快报/预告），用于补足 Tushare 财报更新滞后问题"""
    express_df = data.get('express')
    forecast_df = data.get('forecast')
    income_df = data.get('income')

    latest_income_end = str(sv(income_df, 'end_date', 0, '') or '')
    latest_income_ann = str(sv(income_df, 'ann_date', 0, '') or '')

    candidates = []

    def income_lookup(end_date, field_names):
        if income_df is None or income_df.empty:
            return None
        matches = income_df[income_df['end_date'].astype(str) == str(end_date)]
        if matches.empty:
            return None
        row = matches.iloc[0]
        for field in field_names:
            value = safe_float(row.get(field))
            if value is not None:
                return value
        return None

    def yoy_from_previous_period(current_value, end_date, field_names):
        current_value = safe_float(current_value)
        end_date = str(end_date or '')
        if current_value is None or len(end_date) != 8:
            return None
        prev_end = f"{int(end_date[:4]) - 1}{end_date[4:]}"
        prev_value = income_lookup(prev_end, field_names)
        if prev_value in (None, 0):
            return None
        return (current_value - prev_value) / abs(prev_value) * 100

    if express_df is not None and not express_df.empty:
        express_sorted = express_df.sort_values(['end_date', 'ann_date'], ascending=False).reset_index(drop=True)
        row = express_sorted.iloc[0]
        revenue = safe_float(row.get('revenue'))
        profit = safe_float(row.get('n_income'))
        yoy_revenue = yoy_from_previous_period(revenue, row.get('end_date'), ['revenue', 'total_revenue'])
        yoy_profit = yoy_from_previous_period(profit, row.get('end_date'), ['n_income'])
        candidate = {
            'source_type': 'performance_express',
            'source_title': '业绩快报',
            'report_period': str(row.get('end_date') or ''),
            'disclosure_date': str(row.get('ann_date') or ''),
            'is_estimate': False,
            'metrics': {
                'revenue': {'value': revenue, 'unit': '元', 'yoy': yoy_revenue},
                'net_profit': {'value': profit, 'unit': '元', 'yoy': yoy_profit},
                'total_assets': {'value': safe_float(row.get('total_assets')), 'unit': '元'},
                'eps': {'value': safe_float(row.get('diluted_eps')), 'unit': '元'},
            },
            'summary': str(row.get('perf_summary') or '').strip(),
        }
        candidates.append(candidate)

    if forecast_df is not None and not forecast_df.empty:
        forecast_sorted = forecast_df.sort_values(['end_date', 'ann_date'], ascending=False).reset_index(drop=True)
        row = forecast_sorted.iloc[0]
        # Tushare forecast 接口的净利润区间通常是“万元”口径，这里统一换算成“元”
        net_min = safe_float(row.get('net_profit_min'))
        net_max = safe_float(row.get('net_profit_max'))
        if net_min is not None:
            net_min *= 10000
        if net_max is not None:
            net_max *= 10000
        p_min = safe_float(row.get('p_change_min'))
        p_max = safe_float(row.get('p_change_max'))
        last_parent = safe_float(row.get('last_parent_net'))
        if last_parent is not None:
            last_parent *= 10000
        candidate = {
            'source_type': 'performance_forecast',
            'source_title': f"业绩预告（{str(row.get('type') or '').strip() or '未注明类型'}）",
            'report_period': str(row.get('end_date') or ''),
            'disclosure_date': str(row.get('ann_date') or row.get('first_ann_date') or ''),
            'is_estimate': True,
            'metrics': {
                'net_profit': {
                    'min': net_min,
                    'max': net_max,
                    'unit': '元',
                    'yoy_min': p_min,
                    'yoy_max': p_max,
                    'last_parent_net': last_parent,
                },
            },
            'summary': str(row.get('summary') or '').strip(),
            'change_reason': str(row.get('change_reason') or '').strip(),
        }
        candidates.append(candidate)

    if not candidates:
        return None

    def candidate_key(item):
        report_period = item.get('report_period') or ''
        disclosure_date = item.get('disclosure_date') or ''
        priority = 1 if item.get('source_type') == 'performance_express' else 0
        return (report_period, disclosure_date, priority)

    latest = sorted(candidates, key=candidate_key, reverse=True)[0]

    # 如果财报正式数据已经覆盖同一或更晚报告期，且公告不更新，则不强行覆盖
    if latest_income_end and latest.get('report_period'):
        if latest_income_end > latest['report_period']:
            return None
        if latest_income_end == latest['report_period'] and latest_income_ann and latest.get('disclosure_date') and latest_income_ann >= latest['disclosure_date']:
            return None

    return latest


def merge_latest_disclosures(primary, secondary):
    """合并 Tushare 与巨潮公告披露，显式使用来源优先级"""
    candidates = [item for item in [primary, secondary] if item]
    if not candidates:
        return None

    def source_rank(item):
        if item.get('source_priority') is not None:
            return int(item.get('source_priority') or 0)
        source_type = str(item.get('source_type') or '')
        ranking = {
            'cninfo_annual_report_summary': 70,
            'cninfo_annual_report_full': 65,
            'cninfo_periodic_report': 60,
            'cninfo_performance_express': 50,
            'performance_express': 40,
            'cninfo_performance_forecast': 30,
            'performance_forecast': 20,
        }
        return ranking.get(source_type, 10)

    def key_fn(item):
        report_period = str(item.get('report_period') or '')
        disclosure_date = str(item.get('disclosure_date') or '')
        return (report_period, source_rank(item), disclosure_date)

    return sorted(candidates, key=key_fn, reverse=True)[0]


def analyze_latest_disclosure(disclosure):
    if not disclosure:
        return None

    lines = ["### ⚡ 最新公告补丁\n"]
    source_title = disclosure.get('source_title') or disclosure.get('source_type') or '最新披露'
    report_period = disclosure.get('report_period') or '未知期间'
    disclosure_date = disclosure.get('disclosure_date') or '未知披露日'
    source_level = disclosure.get('source_level') or 'unknown'
    lines.append(f"最新披露来源：**{source_title}** ｜ 报告期：**{report_period}** ｜ 披露日：**{disclosure_date}**\n")
    lines.append(f"数据等级：**{source_level}**\n")

    net_profit = (disclosure.get('metrics') or {}).get('net_profit') or {}
    revenue = (disclosure.get('metrics') or {}).get('revenue') or {}

    source_type = str(disclosure.get('source_type') or '')

    if 'express' in source_type:
        if revenue.get('value') is not None:
            yoy = revenue.get('yoy')
            lines.append(f"- 营收：**{fmt(revenue['value'])}**" + (f"（同比 {yoy:+.1f}%）" if yoy is not None else ""))
        if net_profit.get('value') is not None:
            yoy = net_profit.get('yoy')
            lines.append(f"- 净利润：**{fmt(net_profit['value'])}**" + (f"（同比 {yoy:+.1f}%）" if yoy is not None else ""))
        if disclosure.get('summary'):
            lines.append(f"- 快报摘要：{disclosure['summary']}")
        lines.append("")
        lines.append("💡 如果正式年报尚未完全同步到结构化财报源，写作时优先参考这份业绩快报。")
        return '\n'.join(lines)

    if 'forecast' in source_type:
        np_min = net_profit.get('min')
        np_max = net_profit.get('max')
        yoy_min = net_profit.get('yoy_min')
        yoy_max = net_profit.get('yoy_max')
        if np_min is not None and np_max is not None:
            range_text = f"**{fmt(np_min)} ~ {fmt(np_max)}**"
            yoy_text = ""
            if yoy_min is not None and yoy_max is not None:
                yoy_text = f"（同比 {yoy_min:+.1f}% ~ {yoy_max:+.1f}%）"
            lines.append(f"- 预计净利润区间：{range_text}{yoy_text}")
        if disclosure.get('summary'):
            lines.append(f"- 预告摘要：{disclosure['summary']}")
        if disclosure.get('change_reason'):
            lines.append(f"- 变动原因：{disclosure['change_reason']}")
        lines.append("")
        lines.append("⚠️ 该口径来自业绩预告，写作时必须使用“预计/区间/预告显示”等表达，不能写成正式年报已披露的确定值。")
        return '\n'.join(lines)

    if source_type == 'cninfo_annual_report_summary':
        if revenue.get('value') is not None:
            lines.append(f"- 营收：**{fmt(revenue['value'])}**")
        if net_profit.get('value') is not None:
            lines.append(f"- 净利润：**{fmt(net_profit['value'])}**")
        if disclosure.get('summary'):
            lines.append(f"- 公告标题：{disclosure['summary']}")
        if disclosure.get('source_url'):
            lines.append(f"- 公告链接：{disclosure['source_url']}")
        lines.append("")
        lines.append("💡 该口径来自巨潮正式披露文件，优先级高于结构化补丁源。")
        return '\n'.join(lines)

    return None


def get_annual(df, n=3):
    """筛选年报数据（end_date 以 1231 结尾），返回最近 n 年，按时间倒序。
    v2: 增加 end_date 去重，避免同一年份出现多行导致数据错误。
    """
    if df is None or df.empty:
        return None
    if 'end_date' not in df.columns:
        return df.drop_duplicates().head(n)
    annual = df[df['end_date'].astype(str).str.endswith('1231')].copy()
    # 去重：同一 end_date 只保留第一条
    annual = annual.drop_duplicates(subset=['end_date'], keep='first')
    annual = annual.sort_values('end_date', ascending=False).head(n).reset_index(drop=True)
    if annual.empty:
        annual = df.sort_values('end_date', ascending=False).drop_duplicates(subset=['end_date'], keep='first').head(n).reset_index(drop=True)
    return annual


def get_latest_quarterly(df):
    """获取比最新年报更新的季报数据，以及去年同期数据用于同比
    返回 (latest_q, same_q_prev_year, q_label) 或 (None, None, None)
    """
    if df is None or df.empty or 'end_date' not in df.columns:
        return None, None, None

    df_sorted = df.sort_values('end_date', ascending=False).reset_index(drop=True)
    dates = df_sorted['end_date'].astype(str).tolist()

    # 找最新年报的年份
    annual_years = [d[:4] for d in dates if d.endswith('1231')]
    latest_annual_year = int(annual_years[0]) if annual_years else 0

    # 找比最新年报更新的季报（不含年报）
    q_suffix_labels = {'0331': 'Q1', '0630': 'Q2（半年报）', '0930': 'Q3'}
    latest_q = None
    latest_q_date = None
    for d in dates:
        year = int(d[:4])
        suffix = d[4:]
        if year > latest_annual_year and suffix in q_suffix_labels:
            latest_q_date = d
            latest_q = df_sorted[df_sorted['end_date'].astype(str) == d].iloc[0:1].reset_index(drop=True)
            break

    if latest_q is None or latest_q.empty:
        return None, None, None

    # 找去年同期
    suffix = latest_q_date[4:]
    prev_year_date = str(int(latest_q_date[:4]) - 1) + suffix
    prev_q = df_sorted[df_sorted['end_date'].astype(str) == prev_year_date]
    prev_q = prev_q.iloc[0:1].reset_index(drop=True) if not prev_q.empty else None

    q_label = f"{latest_q_date[:4]}年{q_suffix_labels.get(suffix, suffix)}"
    return latest_q, prev_q, q_label


def analyze_quarterly_update(fi, inc, bs, cf):
    """⚡ 最新季报跟踪：对比最新季报 vs 去年同期"""
    # 尝试从多个数据源获取季报
    q_fi, prev_fi, label_fi = get_latest_quarterly(fi)
    q_inc, prev_inc, label_inc = get_latest_quarterly(inc)
    q_bs, prev_bs, label_bs = get_latest_quarterly(bs)

    # 选择有数据的标签
    q_label = label_fi or label_inc or label_bs
    if q_label is None:
        return None  # 没有比年报更新的季报数据

    lines = ["### ⚡ 最新季报跟踪\n"]
    lines.append(f"以上趋势分析基于近3年年报。以下是**{q_label}**的最新数据，"
                 f"与去年同期对比：\n")

    changes = []  # 存储重大变化

    # --- 利润表对比 ---
    if q_inc is not None and prev_inc is not None:
        metrics_inc = [
            ('营收', 'revenue', 'total_revenue'),
            ('净利润', 'n_income', None),
            ('营业利润', 'operate_profit', None),
        ]
        inc_rows = []
        for name, col, alt_col in metrics_inc:
            curr_v = sv(q_inc, col, 0) or (sv(q_inc, alt_col, 0) if alt_col else None)
            prev_v = sv(prev_inc, col, 0) or (sv(prev_inc, alt_col, 0) if alt_col else None)
            if curr_v is not None and prev_v is not None and prev_v != 0:
                chg = (curr_v - prev_v) / abs(prev_v) * 100
                emoji = '🟢' if chg > 10 else ('🔴' if chg < -10 else '➡️')
                inc_rows.append(f"| {name} | {fmt(curr_v)} | {fmt(prev_v)} | {chg:+.1f}% {emoji} |")
                if abs(chg) >= 20:
                    direction = '增长' if chg > 0 else '下降'
                    changes.append(f"{name}同比{direction}{abs(chg):.0f}%")

        if inc_rows:
            lines.append("| 指标 | 最新季报 | 去年同期 | 同比变化 |")
            lines.append("|------|---------|---------|---------|")
            lines.extend(inc_rows)
            lines.append("")

    # --- 财务指标对比 ---
    if q_fi is not None and prev_fi is not None:
        metrics_fi = [
            ('ROE', 'roe', '%'),
            ('毛利率', 'grossprofit_margin', '%'),
            ('净利率', 'netprofit_margin', '%'),
            ('资产负债率', 'debt_to_assets', '%'),
        ]
        fi_rows = []
        for name, col, unit in metrics_fi:
            curr_v = sv(q_fi, col, 0)
            prev_v = sv(prev_fi, col, 0)
            if curr_v is not None and prev_v is not None:
                diff = curr_v - prev_v
                emoji = '🟢' if diff > 2 else ('🔴' if diff < -2 else '➡️')
                fi_rows.append(f"| {name} | {curr_v:.2f}{unit} | {prev_v:.2f}{unit} | {diff:+.2f}pp {emoji} |")
                if abs(diff) >= 5:
                    direction = '提升' if diff > 0 else '下降'
                    changes.append(f"{name}{direction}{abs(diff):.1f}个百分点")

        if fi_rows:
            lines.append("| 指标 | 最新季报 | 去年同期 | 变动 |")
            lines.append("|------|---------|---------|------|")
            lines.extend(fi_rows)
            lines.append("")

    # --- 资产负债表快照 ---
    if q_bs is not None:
        total_assets = sv(q_bs, 'total_assets', 0)
        total_liab = sv(q_bs, 'total_liab', 0)
        if total_assets and total_liab:
            curr_debt_ratio = total_liab / total_assets * 100
            lines.append(f"最新总资产 **{fmt(total_assets)}**，"
                         f"总负债 **{fmt(total_liab)}**，"
                         f"实时负债率 **{curr_debt_ratio:.1f}%**")
            # 和年报对比
            annual_bs = get_annual(bs)
            if annual_bs is not None:
                annual_debt = sv(annual_bs, 'debt_to_assets', 0)
                if annual_debt is None:
                    a_total = sv(annual_bs, 'total_assets', 0)
                    a_liab = sv(annual_bs, 'total_liab', 0)
                    annual_debt = a_liab / a_total * 100 if a_total and a_liab else None
                if annual_debt is not None:
                    diff = curr_debt_ratio - annual_debt
                    if abs(diff) > 3:
                        direction = '上升' if diff > 0 else '下降'
                        lines.append(f"较年报{direction} {abs(diff):.1f} 个百分点")
            lines.append("")

    # --- 趋势判断 ---
    annual_fi = get_annual(fi)
    if changes:
        lines.append("**⚠️ 注意！以下指标与年报趋势出现显著变化：**\n")
        for c in changes:
            lines.append(f"- 📌 {c}")
        lines.append("")
        lines.append("💡 这意味着上文基于年报的部分判断可能需要修正。"
                     "建议重点关注最新季报反映的新变化。")
    else:
        lines.append("💡 最新季报数据与年报趋势**基本一致**，年报分析结论仍然有效。")

    return '\n'.join(lines)


def analyze_business_segments(mainbz):
    """主营业务构成分析：对比最新披露 vs 去年同期"""
    if mainbz is None or mainbz.empty:
        return None

    df = mainbz.copy()
    df = df[df['bz_item'].notna()].copy()
    if df.empty:
        return None

    # v2: 去重 - 同一期间同一业务只保留第一条
    df['end_date'] = df['end_date'].astype(str)
    df = df.drop_duplicates(subset=['end_date', 'bz_item'], keep='first')

    # 按 end_date 排序，找最新披露期
    periods = sorted(df['end_date'].unique(), reverse=True)
    if len(periods) < 2:
        return None

    latest_period = periods[0]
    latest_suffix = latest_period[4:]  # e.g. '0630' or '1231'
    latest_year = int(latest_period[:4])

    # 找去年同期
    prev_period = str(latest_year - 1) + latest_suffix
    if prev_period not in periods:
        prev_period = periods[1] if len(periods) > 1 else None

    if prev_period is None:
        return None

    latest_df = df[df['end_date'] == latest_period].sort_values('bz_sales', ascending=False)
    prev_df = df[df['end_date'] == prev_period].set_index('bz_item') if prev_period else None

    # 生成报告
    period_labels = {'1231': '年报', '0630': '半年报', '0331': 'Q1', '0930': 'Q3'}
    latest_label = f"{latest_year}年{period_labels.get(latest_suffix, latest_suffix)}"
    prev_label = f"{latest_year-1}年{period_labels.get(latest_suffix, latest_suffix)}" if prev_period else '上期'

    lines = ["### 主营业务构成\n"]
    lines.append(f"最新披露：**{latest_label}** vs {prev_label}\n")
    lines.append("| 业务 | 最新营收 | 去年同期 | 同比变化 | 毛利率 |")
    lines.append("|------|---------|---------|---------|------|")

    highlights = []
    total_latest = 0
    for _, row in latest_df.iterrows():
        bz_item = row['bz_item']
        sales = row['bz_sales'] if row['bz_sales'] == row['bz_sales'] else 0
        profit = row['bz_profit'] if row['bz_profit'] == row['bz_profit'] else 0
        total_latest += sales

        # 去年同期
        prev_sales = None
        if prev_df is not None and bz_item in prev_df.index:
            prev_row = prev_df.loc[bz_item]
            if isinstance(prev_row, pd.DataFrame):  # 多行（重复bz_item）
                prev_sales = prev_row.iloc[0]['bz_sales']
            elif isinstance(prev_row, pd.Series):
                prev_sales = prev_row['bz_sales']
            if prev_sales is not None and prev_sales != prev_sales:  # NaN
                prev_sales = None

        # 计算同比
        if prev_sales and prev_sales > 0 and sales:
            chg = (sales - prev_sales) / abs(prev_sales) * 100
            emoji = '🟢' if chg > 20 else ('🔴' if chg < -20 else '➡️')
            chg_str = f"{chg:+.1f}% {emoji}"
            if abs(chg) >= 30:
                direction = '暴增' if chg > 0 else '大幅萎缩'
                highlights.append(f"**{bz_item}** {direction}{abs(chg):.0f}%（{fmt(prev_sales)}→{fmt(sales)}）")
        else:
            chg_str = '—'

        # 毛利率
        gm = f"{profit/sales*100:.1f}%" if sales and sales > 0 else '—'

        lines.append(f"| {bz_item} | {fmt(sales)} | {fmt(prev_sales) if prev_sales else '—'} | {chg_str} | {gm} |")

    # 解读
    lines.append("")
    if highlights:
        lines.append("⚡ **重大业务变化：**\n")
        for h in highlights:
            lines.append(f"- {h}")
        lines.append("")

    # 营收集中度
    if total_latest > 0 and not latest_df.empty:
        top_sales = latest_df.iloc[0]['bz_sales']
        top_name = latest_df.iloc[0]['bz_item']
        top_ratio = top_sales / total_latest * 100 if top_sales == top_sales else 0
        if top_ratio > 60:
            lines.append(f"💡 **{top_name}**贡献了 {top_ratio:.0f}% 的营收，营收高度集中。")
        elif top_ratio > 40:
            lines.append(f"💡 **{top_name}**是第一大业务，贡献 {top_ratio:.0f}% 营收。")

    return '\n'.join(lines)


# ─── 分析模块 ──────────────────────────────────────────────────────────────────

def analyze_trends(fi):
    """3年趋势表"""
    annual = get_annual(fi)
    if annual is None or len(annual) < 2:
        return "### 三年趋势\n\n数据不足，无法生成趋势分析。\n"

    years = [year_label(sv(annual, 'end_date', i)) for i in range(len(annual))]
    metrics = [
        ('ROE', 'roe', '%'),
        ('毛利率', 'grossprofit_margin', '%'),
        ('净利率', 'netprofit_margin', '%'),
        ('资产负债率', 'debt_to_assets', '%'),
        ('流动比率', 'current_ratio', ''),
        ('营收增速', 'or_yoy', '%'),
        ('净利润增速', 'netprofit_yoy', '%'),
    ]

    lines = ["### 三年关键指标趋势\n"]
    lines.append(f"| 指标 | {' | '.join(years)} | 趋势 |")
    lines.append(f"|------|" + "------|" * len(years) + "------|")

    for label, col, unit in metrics:
        vals = [sv(annual, col, i) for i in range(len(annual))]
        cells = []
        for v in vals:
            if v is not None:
                cells.append(f"{v:.2f}{unit}")
            else:
                cells.append("—")

        # 计算趋势（最新 vs 最早）
        if vals[0] is not None and vals[-1] is not None:
            diff = vals[0] - vals[-1]
            trend = '📈 上升' if diff > 1 else ('📉 下降' if diff < -1 else '➡️ 稳定')
        else:
            trend = '—'
        lines.append(f"| **{label}** | {' | '.join(cells)} | {trend} |")

    # 趋势解读
    latest_roe = sv(annual, 'roe', 0)
    oldest_roe = sv(annual, 'roe', len(annual)-1)
    latest_growth = sv(annual, 'or_yoy', 0)
    oldest_growth = sv(annual, 'or_yoy', len(annual)-1)

    lines.append("")
    if latest_roe and oldest_roe:
        diff = latest_roe - oldest_roe
        if diff > 2:
            lines.append(f"💡 ROE 三年提升 {diff:.1f} 个百分点，盈利能力在增强。")
        elif diff < -2:
            lines.append(f"⚠️ ROE 三年下滑 {abs(diff):.1f} 个百分点，盈利能力在减弱。")

    if latest_growth is not None and oldest_growth is not None:
        if latest_growth < oldest_growth and latest_growth < 10:
            lines.append(f"⚠️ 营收增速从 {oldest_growth:.1f}% 降至 {latest_growth:.1f}%，增长动力在减弱。")

    return '\n'.join(lines)


def analyze_asset_structure(bs):
    """资产结构分析"""
    annual = get_annual(bs)
    if annual is None or annual.empty:
        return "### 资产结构\n\n数据不足。\n"

    total = sv(annual, 'total_assets', 0)
    if total is None or total == 0:
        return "### 资产结构\n\n数据不足。\n"

    cash = sv(annual, 'money_cap', 0, 0)
    recv = sv(annual, 'accounts_receiv', 0, 0)
    inv = sv(annual, 'inventories', 0, 0)
    goodwill = sv(annual, 'goodwill', 0, 0)
    cur = sv(annual, 'total_cur_assets', 0, 0)
    nca = sv(annual, 'total_nca', 0, 0)

    cash_r = cash / total * 100 if cash else 0
    recv_r = recv / total * 100 if recv else 0
    inv_r = inv / total * 100 if inv else 0
    gw_r = goodwill / total * 100 if goodwill else 0
    cur_r = cur / total * 100 if cur else 0

    lines = [
        "### 资产结构透视\n",
        f"总资产 **{fmt(total, '元')}**，构成如下：\n",
        f"| 科目 | 金额 | 占总资产 | 判断 |",
        f"|------|------|----------|------|",
        f"| 货币资金 | {fmt(cash, '元')} | {cash_r:.1f}% | {'🟢 充裕' if cash_r > 15 else '🟡 一般'} |",
        f"| 应收账款 | {fmt(recv, '元')} | {recv_r:.1f}% | {'🟢 极低' if recv_r < 5 else '🟡 适中' if recv_r < 15 else '🟠 偏高'} |",
        f"| 存货 | {fmt(inv, '元')} | {inv_r:.1f}% | {'🟢 正常' if inv_r < 30 else '🟡 偏高'} |",
        f"| 商誉 | {fmt(goodwill, '元')} | {gw_r:.1f}% | {'🟢 无' if gw_r < 1 else '🟡 有' if gw_r < 10 else '🔴 高危'} |",
        f"| 流动/非流动 | {cur_r:.0f}% / {100-cur_r:.0f}% | — | {'轻资产' if cur_r > 60 else '重资产'} |",
    ]

    # 解读
    lines.append("")
    if cash_r > 20:
        lines.append(f"💡 货币资金占总资产 {cash_r:.0f}%，\"现金奶牛\"名不虚传。")
    if recv_r < 3:
        lines.append(f"💡 应收账款几乎为零——做的是先款后货的生意，话语权极强。")
    elif recv_r > 20:
        lines.append(f"⚠️ 应收账款占比 {recv_r:.0f}%，回款风险需关注。")
    if gw_r > 10:
        lines.append(f"🔴 商誉占总资产 {gw_r:.0f}%！这是一颗\"减值地雷\"。")

    return '\n'.join(lines)


def analyze_expense_structure(inc):
    """费用结构分析"""
    annual = get_annual(inc)
    if annual is None or annual.empty:
        return "### 费用结构\n\n数据不足。\n"

    lines = ["### 费用结构分析\n"]

    years_data = []
    for i in range(min(3, len(annual))):
        rev = sv(annual, 'revenue', i) or sv(annual, 'total_revenue', i)
        sell = sv(annual, 'sell_exp', i, 0)
        admin = sv(annual, 'admin_exp', i, 0)
        rd = sv(annual, 'rd_exp', i, 0)
        fin = sv(annual, 'fin_exp', i, 0)
        year = year_label(sv(annual, 'end_date', i))

        if rev and rev > 0:
            years_data.append({
                'year': year, 'rev': rev,
                'sell_r': sell / rev * 100,
                'admin_r': admin / rev * 100,
                'rd_r': rd / rev * 100 if rd else 0,
                'fin_r': fin / rev * 100,
            })

    if not years_data:
        return "### 费用结构\n\n数据不足。\n"

    lines.append("| 费用率 | " + " | ".join(d['year'] for d in years_data) + " | 趋势 |")
    lines.append("|--------|" + "--------|" * len(years_data) + "--------|")

    for label, key in [('销售费用率', 'sell_r'), ('管理费用率', 'admin_r'), ('研发费用率', 'rd_r'), ('财务费用率', 'fin_r')]:
        vals = [d[key] for d in years_data]
        cells = [f"{v:.2f}%" for v in vals]
        if len(vals) >= 2:
            diff = vals[0] - vals[-1]
            trend = '📈' if diff > 1 else ('📉' if diff < -1 else '➡️')
        else:
            trend = '—'
        lines.append(f"| **{label}** | {' | '.join(cells)} | {trend} |")

    # 解读
    lines.append("")
    if years_data:
        d = years_data[0]
        if d['sell_r'] > 15:
            lines.append(f"⚠️ 销售费用率 {d['sell_r']:.1f}%，获客成本偏高。")
        elif d['sell_r'] < 5:
            lines.append(f"💡 销售费用率仅 {d['sell_r']:.1f}%——产品自带销售力，不需要砸钱推广。")
        if len(years_data) >= 2 and years_data[0]['sell_r'] > years_data[-1]['sell_r'] + 3:
            lines.append(f"⚠️ 销售费用率上升明显，可能在\"烧钱冲量\"。")

    return '\n'.join(lines)


def analyze_profit_quality(fi, inc, cf):
    """利润质量检验"""
    fi_annual = get_annual(fi)
    inc_annual = get_annual(inc)
    cf_annual = get_annual(cf)

    lines = ["### 利润质量检验\n"]
    lines.append("这是最关键的风控视角——赚的钱是\"真金白银\"还是\"纸面富贵\"？\n")

    # OCF/净利润 比值趋势
    ocf_ratios = []
    for i in range(min(3, len(fi_annual) if fi_annual is not None else 0)):
        r = sv(fi_annual, 'ocf_to_profit', i)
        y = year_label(sv(fi_annual, 'end_date', i))
        if r is not None:
            ocf_ratios.append((y, r))

    if ocf_ratios:
        lines.append("**经营现金流 / 净利润 比值：**\n")
        for y, r in ocf_ratios:
            bar = '█' * max(1, min(20, int(r / 5))) if r > 0 else '░' * 5
            lines.append(f"- {y}年：**{r:.1f}%** {bar}")

        latest_r = ocf_ratios[0][1]
        lines.append("")
        if latest_r > 100:
            lines.append("💡 经营现金流 > 净利润，利润质量**优秀**——赚到的都是真金白银。")
        elif latest_r > 70:
            lines.append("🟢 经营现金流接近净利润，利润质量良好。")
        elif latest_r > 30:
            lines.append("🟡 经营现金流明显低于净利润，部分利润可能还\"在路上\"（应收账款等）。")
        else:
            lines.append("🔴 经营现金流远低于净利润——**利润质量存疑**！赚的都是\"纸面利润\"。")

    # 应收 vs 营收增速对比
    if inc_annual is not None and len(inc_annual) >= 2:
        rev0 = sv(inc_annual, 'revenue', 0) or sv(inc_annual, 'total_revenue', 0)
        rev1 = sv(inc_annual, 'revenue', 1) or sv(inc_annual, 'total_revenue', 1)
        if rev0 and rev1 and rev1 > 0:
            rev_growth = (rev0 - rev1) / abs(rev1) * 100
            lines.append(f"\n**营收增速**：{rev_growth:.1f}%")

            # 如果有应收数据
            from_bs_needed = True  # 标记需要从资产负债表获取应收数据

    # 现金流组合判断
    if cf_annual is not None and not cf_annual.empty:
        ocf = sv(cf_annual, 'n_cashflow_act', 0)
        icf = sv(cf_annual, 'n_cashflow_inv_act', 0)
        fcf_fin = sv(cf_annual, 'n_cash_flows_fnc_act', 0)
        free_cf = sv(cf_annual, 'free_cashflow', 0)

        lines.append(f"\n**现金流三维图谱（最新年度）：**\n")
        lines.append(f"- 经营现金流：**{fmt(ocf)}** {'✅' if ocf and ocf > 0 else '❌'}")
        lines.append(f"- 投资现金流：**{fmt(icf)}** {'（在扩张）' if icf and icf < 0 else '（在收缩）'}")
        lines.append(f"- 筹资现金流：**{fmt(fcf_fin)}** {'（在融资）' if fcf_fin and fcf_fin > 0 else '（在分红/还债）'}")
        lines.append(f"- 自由现金流：**{fmt(free_cf)}**")

        lines.append("")
        if ocf and icf and fcf_fin:
            if ocf > 0 and icf < 0 and fcf_fin < 0:
                lines.append("💡 **成熟现金奶牛**：经营造血 → 适度投资 → 大额分红/还债。这是最健康的现金流模式。")
            elif ocf > 0 and icf < 0 and fcf_fin > 0:
                lines.append("💡 **快速扩张期**：经营造血 + 外部融资 → 大举投资。增长潜力大，但需关注扩张效率。")
            elif ocf < 0 and icf < 0 and fcf_fin > 0:
                lines.append("🔴 **危险模式**：经营不造血，还在投资扩张，全靠融资续命。高度警惕！")
            elif ocf > 0 and icf > 0 and fcf_fin < 0:
                lines.append("🟡 **收缩/转型期**：经营造血 + 变卖资产 → 偿债。关注转型是否成功。")

    return '\n'.join(lines)


def analyze_dupont(fi):
    """杜邦分析：ROE 分解"""
    annual = get_annual(fi)
    if annual is None or annual.empty:
        return "### 杜邦分析\n\n数据不足。\n"

    roe = sv(annual, 'roe', 0)
    npm = sv(annual, 'netprofit_margin', 0)
    at = sv(annual, 'assets_turn', 0)
    debt = sv(annual, 'debt_to_assets', 0)

    if roe is None or npm is None:
        return "### 杜邦分析\n\n数据不足。\n"

    # 计算权益乘数
    equity_mult = 1 / (1 - debt / 100) if debt and debt < 100 else None

    lines = [
        "### 杜邦分析（ROE 分解）\n",
        "ROE 不只是一个数字，拆开看才知道公司**靠什么赚钱**：\n",
        "```",
        f"ROE {pct(roe)} = 净利率 {pct(npm)} × 周转率 {fmt(at, '次', 2) if at else '—'} × 杠杆 {f'{equity_mult:.2f}倍' if equity_mult else '—'}",
        "```\n",
    ]

    # 判断驱动因素
    drivers = []
    if npm and npm > 20:
        drivers.append("高利润率")
    if at and at > 1:
        drivers.append("高周转")
    if equity_mult and equity_mult > 2.5:
        drivers.append("高杠杆")

    if drivers:
        lines.append(f"💡 ROE 的核心驱动力是：**{'、'.join(drivers)}**。")
        if "高利润率" in drivers and "高杠杆" not in drivers:
            lines.append("这是最优质的盈利模式——靠产品赚钱，而不是靠借钱撬动。")
        elif "高杠杆" in drivers and "高利润率" not in drivers:
            lines.append("⚠️ ROE 主要靠杠杆撬动，一旦行业下行，风险会放大。")
    else:
        lines.append("各项驱动因素均衡，无明显短板。")

    return '\n'.join(lines)


def analyze_solvency_trend(fi, bs):
    """偿债能力（含趋势）"""
    annual = get_annual(fi)
    bs_annual = get_annual(bs)
    if annual is None:
        return "### 偿债能力\n\n数据不足。\n"

    lines = ["### 偿债能力\n"]

    debt = sv(annual, 'debt_to_assets', 0)
    cr = sv(annual, 'current_ratio', 0)
    qr = sv(annual, 'quick_ratio', 0)

    debt_label = '🟢 低杠杆' if debt and debt < 40 else ('🟡 适中' if debt and debt < 60 else ('🟠 偏高' if debt and debt < 75 else '🔴 高危'))
    cr_label = '🟢 充裕' if cr and cr > 2 else ('🟢 健康' if cr and cr > 1.5 else ('🟡 一般' if cr and cr > 1 else '🔴 紧张'))

    lines.append(f"- **资产负债率**：{pct(debt)} → {debt_label}")
    lines.append(f"- **流动比率**：{fmt(cr, '', 2) if cr else '—'} → {cr_label}")
    lines.append(f"- **速动比率**：{fmt(qr, '', 2) if qr else '—'}")

    # 趋势
    if len(annual) >= 2:
        prev_debt = sv(annual, 'debt_to_assets', len(annual)-1)
        if debt is not None and prev_debt is not None:
            diff = debt - prev_debt
            y_old = year_label(sv(annual, 'end_date', len(annual)-1))
            if abs(diff) > 0.5:
                direction = "上升" if diff > 0 else "下降"
                lines.append(f"- 较 {y_old} 年{direction} {abs(diff):.1f} 个百分点")

    # 解读
    lines.append("")
    if debt and debt < 20:
        ttl = sv(bs_annual, 'total_assets', 0) if bs_annual is not None else None
        tlb = sv(bs_annual, 'total_liab', 0) if bs_annual is not None else None
        lines.append(f"💡 {pct(debt)} 的资产负债率，意味着 {fmt(ttl, '元')} 的总资产中，借来的钱只有 {fmt(tlb, '元')}。几乎不靠负债经营。")
    elif debt and debt > 70:
        lines.append(f"🔴 资产负债率高达 {pct(debt)}！如果行业遇冷，偿债压力会迅速放大。")

    return '\n'.join(lines)


def analyze_profitability_trend(fi):
    """盈利能力（含趋势）"""
    annual = get_annual(fi)
    if annual is None:
        return "### 盈利能力\n\n数据不足。\n"

    roe = sv(annual, 'roe', 0)
    gm = sv(annual, 'grossprofit_margin', 0)
    npm = sv(annual, 'netprofit_margin', 0)

    roe_label = '🟢 卓越' if roe and roe > 20 else ('🟢 优秀' if roe and roe > 15 else ('🟢 良好' if roe and roe > 10 else ('🟡 一般' if roe and roe > 5 else '🔴 较差')))

    lines = [
        "### 盈利能力\n",
        f"- **ROE**：{pct(roe)} → {roe_label}",
        f"- **毛利率**：{pct(gm)}",
        f"- **净利率**：{pct(npm)}",
    ]

    if gm and npm:
        spread = gm - npm
        lines.append(f"- 毛利率到净利率的\"损耗\"：{spread:.1f} 个百分点（费用+税收）")

    lines.append("")
    if gm and gm > 80:
        lines.append(f"💡 {pct(gm)} 的毛利率，在全A股都属于顶级。意味着每收入100元，原材料成本不到 {100-gm:.0f} 元。")
    if roe and roe > 20:
        lines.append(f"💡 ROE {pct(roe)}，远超巴菲特的15%\"优秀线\"。")

    return '\n'.join(lines)


def analyze_growth_trend(fi):
    """成长能力"""
    annual = get_annual(fi)
    if annual is None:
        return "### 成长能力\n\n数据不足。\n"

    rev_g = sv(annual, 'or_yoy', 0)
    np_g = sv(annual, 'netprofit_yoy', 0)
    op_g = sv(annual, 'op_yoy', 0)

    label = '🟢 爆发式' if rev_g and rev_g > 50 else ('🟢 高增长' if rev_g and rev_g > 20 else ('🟢 稳健' if rev_g and rev_g > 10 else ('🟡 低增长' if rev_g and rev_g > 0 else '🔴 负增长')))

    lines = [
        "### 成长能力\n",
        f"- **营收增速**：{pct(rev_g)} → {label}",
        f"- **净利润增速**：{pct(np_g)}",
        f"- **营业利润增速**：{pct(op_g)}",
    ]

    # 增收不增利预警
    lines.append("")
    if rev_g is not None and np_g is not None:
        if rev_g > 5 and np_g < 0:
            lines.append("🔴 **增收不增利**！营收在增长，利润却在下滑——要么成本失控，要么在\"赔本赚吆喝\"。")
        elif rev_g < 0 and np_g > 10:
            lines.append("💡 营收下降但利润增长——可能在砍掉低毛利业务，优化利润结构。")
        elif rev_g is not None and rev_g < 10:
            latest_y = year_label(sv(annual, 'end_date', 0))
            if len(annual) >= 3:
                old_g = sv(annual, 'or_yoy', 2)
                if old_g and old_g > 15:
                    lines.append(f"⚠️ 增速从 {old_g:.0f}% 降至 {rev_g:.0f}%，增长动力明显减弱。天花板要来了？")

    return '\n'.join(lines)


def analyze_audit_holder(audit, trade):
    """审计意见 + 增减持"""
    lines = ["### 审计意见与股东动向\n"]

    # 审计
    if audit is not None and not audit.empty:
        for i in range(min(3, len(audit))):
            result = sv(audit, 'audit_result', i, '未知')
            agency = sv(audit, 'audit_agency', i, '未知')
            period_end = sv(audit, 'end_date', i, None)
            announce_date = sv(audit, 'ann_date', i, '未知')
            year_label = str(period_end)[:4] if period_end else str(announce_date)[:4]
            emoji = '🟢' if result == '标准无保留意见' else ('🔴' if '保留' in str(result) else '🟡')
            lines.append(f"- {emoji} {year_label}年：{result}（{agency}）")

        non_std = [sv(audit, 'audit_result', i) for i in range(len(audit)) if sv(audit, 'audit_result', i) != '标准无保留意见']
        if non_std:
            lines.append("\n🔴 **存在非标审计意见——这是重大风险信号！**")
        else:
            lines.append("\n连续多年\"清洁审计\"，财务真实性无疑。")
    else:
        lines.append("- 暂无审计数据")

    # 增减持
    lines.append("")
    if trade is not None and not trade.empty:
        inc_c, dec_c, inc_v, dec_v = 0, 0, 0, 0
        for i in range(min(20, len(trade))):
            in_de = sv(trade, 'in_de', i, '')
            vol = sv(trade, 'change_vol', i, 0) or 0
            if in_de == 'IN' or '增' in str(in_de):
                inc_c += 1; inc_v += abs(vol)
            elif in_de == 'DE' or '减' in str(in_de):
                dec_c += 1; dec_v += abs(vol)

        lines.append(f"- 增持 **{inc_c}** 次（{fmt(inc_v, '股')}）| 减持 **{dec_c}** 次（{fmt(dec_v, '股')}）")

        if dec_c > inc_c * 2:
            lines.append("⚠️ 减持多于增持，值得关注。")
        elif inc_c > 0 and dec_c == 0:
            lines.append("💡 只有增持、没有减持——大股东的态度已经很明确了。")
    else:
        lines.append("- 近期无增减持记录")

    return '\n'.join(lines)


def scan_warnings(fi, bs, inc, cf, audit, trade):
    """自动风险预警扫描"""
    fi_a = get_annual(fi)
    bs_a = get_annual(bs)
    inc_a = get_annual(inc)
    cf_a = get_annual(cf)

    warnings = []
    positives = []

    # 1. 应收膨胀检查
    if bs_a is not None and len(bs_a) >= 2 and inc_a is not None and len(inc_a) >= 2:
        recv0 = sv(bs_a, 'accounts_receiv', 0, 0)
        recv1 = sv(bs_a, 'accounts_receiv', 1, 0)
        rev0 = sv(inc_a, 'revenue', 0) or sv(inc_a, 'total_revenue', 0)
        rev1 = sv(inc_a, 'revenue', 1) or sv(inc_a, 'total_revenue', 1)
        if recv0 and recv1 and recv1 > 0 and rev0 and rev1 and rev1 > 0:
            recv_g = (recv0 - recv1) / abs(recv1) * 100
            rev_g = (rev0 - rev1) / abs(rev1) * 100
            if recv_g > rev_g * 1.5 and recv_g > 10:
                warnings.append(f"⚠️ **应收账款增速（{recv_g:.0f}%）远超营收增速（{rev_g:.0f}%）**——可能在\"赊销冲业绩\"")

    # 2. 存货积压检查
    if bs_a is not None and len(bs_a) >= 2 and inc_a is not None and len(inc_a) >= 2:
        inv0 = sv(bs_a, 'inventories', 0, 0)
        inv1 = sv(bs_a, 'inventories', 1, 0)
        rev0 = sv(inc_a, 'revenue', 0) or sv(inc_a, 'total_revenue', 0)
        rev1 = sv(inc_a, 'revenue', 1) or sv(inc_a, 'total_revenue', 1)
        if inv0 and inv1 and inv1 > 0 and rev0 and rev1 and rev1 > 0:
            inv_g = (inv0 - inv1) / abs(inv1) * 100
            rev_g = (rev0 - rev1) / abs(rev1) * 100
            if inv_g > rev_g * 2 and inv_g > 20:
                warnings.append(f"⚠️ **存货增速（{inv_g:.0f}%）远超营收增速（{rev_g:.0f}%）**——可能在滞销")

    # 3. 现金流背离
    if fi_a is not None and not fi_a.empty:
        np_g = sv(fi_a, 'netprofit_yoy', 0)
        ocf_r = sv(fi_a, 'ocf_to_profit', 0)
        if np_g and np_g > 10 and ocf_r and ocf_r < 50:
            warnings.append(f"⚠️ **利润增长 {np_g:.0f}% 但经营现金流/利润仅 {ocf_r:.0f}%**——利润可能\"注水\"")

    # 4. 商誉地雷
    if bs_a is not None:
        gw = sv(bs_a, 'goodwill', 0, 0)
        total = sv(bs_a, 'total_assets', 0, 1)
        if gw and total and gw / total > 0.1:
            warnings.append(f"🔴 **商誉占总资产 {gw/total*100:.0f}%**——一旦业绩不达预期，减值会直接吞噬利润")

    # 5. 审计变脸
    if audit is not None and len(audit) >= 2:
        curr = sv(audit, 'audit_result', 0, '')
        prev = sv(audit, 'audit_result', 1, '')
        if prev == '标准无保留意见' and curr != '标准无保留意见':
            warnings.append(f"🔴 **审计意见从\"标准\"变为\"{curr}\"**——重大风险信号！")

    # 6. 股东减持
    if trade is not None and not trade.empty:
        dec_count = sum(1 for i in range(min(10, len(trade)))
                       if sv(trade, 'in_de', i, '') in ['DE'] or '减' in str(sv(trade, 'in_de', i, '')))
        if dec_count >= 4:
            warnings.append(f"⚠️ **近期减持 {dec_count} 次**——大股东近期有所减持，值得关注")

    # 正面信号
    if fi_a is not None:
        debt = sv(fi_a, 'debt_to_assets', 0)
        roe = sv(fi_a, 'roe', 0)
        gm = sv(fi_a, 'grossprofit_margin', 0)
        if debt and debt < 30: positives.append("低杠杆经营")
        if roe and roe > 15: positives.append("高ROE")
        if gm and gm > 50: positives.append("高毛利")

    if cf_a is not None:
        ocf = sv(cf_a, 'n_cashflow_act', 0)
        if ocf and ocf > 0: positives.append("经营现金流为正")

    return warnings, positives


def generate_score(fi, bs, cf, warnings, positives):
    """综合评分（v2: 适配A股实际水平，中位数公司约60-70分）"""
    fi_a = get_annual(fi)
    cf_a = get_annual(cf)
    scores = {}

    # 偿债能力：A股制造业负债率50-65%属于正常
    debt = sv(fi_a, 'debt_to_assets', 0) if fi_a is not None else None
    if debt is not None:
        scores['偿债能力'] = 95 if debt < 20 else (85 if debt < 40 else (70 if debt < 55 else (60 if debt < 70 else (45 if debt < 80 else 30))))

    # 盈利能力：A股 ROE 中位数约7-8%
    roe = sv(fi_a, 'roe', 0) if fi_a is not None else None
    if roe is not None:
        scores['盈利能力'] = 95 if roe > 20 else (85 if roe > 15 else (70 if roe > 10 else (60 if roe > 5 else (45 if roe > 0 else 30))))

    # 成长能力：正增长就不差
    growth = sv(fi_a, 'or_yoy', 0) if fi_a is not None else None
    if growth is not None:
        scores['成长能力'] = 95 if growth > 30 else (85 if growth > 20 else (70 if growth > 10 else (55 if growth > 0 else 35)))

    # 现金流：分三档更合理
    ocf = sv(cf_a, 'n_cashflow_act', 0) if cf_a is not None else None
    np_val = sv(fi_a, 'netprofit', 0) if fi_a is not None else None
    if ocf is not None:
        if ocf > 0 and np_val and np_val > 0 and ocf / abs(np_val) > 1:
            scores['现金流'] = 90  # 现金流 > 净利润
        elif ocf > 0:
            scores['现金流'] = 75  # 正现金流
        else:
            scores['现金流'] = 35  # 负现金流

    # 风险扣分（降低力度）
    penalty = min(len(warnings) * 3, 15)  # 每项扣3分，最多扣15分

    if scores:
        avg = sum(scores.values()) / len(scores) - penalty
        avg = max(0, min(100, avg))

        if avg >= 80: overall = "🟢 **财务表现优秀**"
        elif avg >= 75: overall = "🟡 **财务表现良好**"
        elif avg >= 65: overall = "🟠 **财务表现中等**"
        else: overall = "🔴 **财务表现待改善**"

        lines = [
            "### 综合评估\n",
            f"**{overall}**（综合 {avg:.0f} 分 / 100）\n",
            "| 维度 | 评分 |",
            "|------|------|",
        ]
        for k, v in scores.items():
            bar = '█' * (v // 10) + '░' * (10 - v // 10)
            lines.append(f"| {k} | {bar} **{v}分** |")

        if warnings:
            lines.append(f"\n⚠️ 风险扣分：{penalty}分（{len(warnings)} 项预警）")

        if positives:
            lines.append(f"\n✅ 正面信号：{' | '.join(positives)}")
        if warnings:
            lines.append(f"\n⚠️ 风险信号：")
            for w in warnings:
                lines.append(f"  {w}")

        return '\n'.join(lines)
    else:
        return "### 综合评估\n\n数据不足，暂无法评估。"


# ─── 主流程 ────────────────────────────────────────────────────────────────────

def run_analysis(ts_code):
    # 判断是否为港股
    if is_hk_code(ts_code):
        print(f"🇭🇰 检测到港股代码: {ts_code}，使用 AKShare 获取财务数据")
        data = fetch_hk_data_akshare(ts_code)
        if data is None:
            print("❌ AKShare 港股数据获取失败，无法生成财务分析")
            sys.exit(1)
        # 转换为 Tushare 兼容格式
        data = adapt_hk_data_to_tushare(data)
    else:
        # A股：走 Tushare 流程
        token = get_token()
        pro = ts.pro_api(token)
        data = fetch_data(pro, ts_code)

    fi = data.get('fina_indicator')
    bs = data.get('balancesheet')
    inc = data.get('income')
    cf = data.get('cashflow')
    audit = data.get('fina_audit')
    trade = data.get('holdertrade')
    basic = data.get('stock_basic')
    mainbz = data.get('fina_mainbz')
    latest_disclosure = derive_latest_disclosure(data)
    name = sv(basic, 'name', default=ts_code)
    cninfo_fetcher = fetch_cninfo_latest_disclosure_external or fetch_cninfo_latest_disclosure
    cninfo_disclosure = cninfo_fetcher(ts_code, name)
    latest_disclosure = merge_latest_disclosures(latest_disclosure, cninfo_disclosure)
    industry = sv(basic, 'industry', default='—')
    list_date = sv(basic, 'list_date', default='—')

    print(f"📝 生成分析报告: {name} ({ts_code})\n")

    # 风险扫描
    warnings, positives = scan_warnings(fi, bs, inc, cf, audit, trade)

    # 季报跟踪
    quarterly_section = analyze_quarterly_update(fi, inc, bs, cf)
    latest_disclosure_section = analyze_latest_disclosure(latest_disclosure)

    # 构建报告
    sections = [
        f"## 九、财务深度分析\n",
        f"**{name}**（{ts_code}）| 行业：{industry} | 上市：{list_date}\n",
        analyze_trends(fi),
        "",
        analyze_solvency_trend(fi, bs),
        "",
        analyze_profitability_trend(fi),
        "",
        analyze_growth_trend(fi),
        "",
        analyze_dupont(fi),
        "",
        analyze_asset_structure(bs),
        "",
    ]
    # 插入主营业务构成（如果有数据）
    biz_section = analyze_business_segments(mainbz)
    if biz_section:
        sections.append(biz_section)
        sections.append("")
    sections += [
        analyze_expense_structure(inc),
        "",
        analyze_profit_quality(fi, inc, cf),
        "",
        analyze_audit_holder(audit, trade),
        "",
        generate_score(fi, bs, cf, warnings, positives),
    ]
    # 如果有季报跟踪数据，插入到综合评估之前
    if quarterly_section:
        sections.insert(-1, "")
        sections.insert(-1, quarterly_section)
    if latest_disclosure_section:
        sections.insert(-1, "")
        sections.insert(-1, latest_disclosure_section)

    report = '\n'.join(sections)

    # 保存
    output_dir = os.environ.get('OUTPUT_DIR',
                                os.path.join(os.path.dirname(__file__), '..', 'output'))
    os.makedirs(output_dir, exist_ok=True)

    rpt_path = os.path.join(output_dir, 'financial_analysis.md')
    with open(rpt_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"✅ 报告: {rpt_path}")

    raw = {}
    for k, df in data.items():
        raw[k] = to_jsonable_payload(df)
    raw['latest_disclosure'] = latest_disclosure or {}
    raw['cninfo_disclosure'] = cninfo_disclosure or {}
    data_path = os.path.join(output_dir, 'financial_data.json')
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(raw, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ 数据: {data_path}")

    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)
    return report


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python3 scripts/financial_analysis.py <股票代码>")
        print("例:   python3 scripts/financial_analysis.py 600519.SH")
        sys.exit(1)
    run_analysis(sys.argv[1])
