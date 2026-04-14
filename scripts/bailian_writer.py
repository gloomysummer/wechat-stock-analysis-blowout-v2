#!/usr/bin/env python3
import json
import pathlib
import sys
import urllib.request

OPENCLAW = pathlib.Path("/root/.openclaw/openclaw.json")
MODELS = ["kimi-k2.5", "qwen3.5-plus", "deepseek-v3.2", "glm-5"]


def load_provider():
    obj = json.loads(OPENCLAW.read_text())
    provider = obj["models"]["providers"]["qwencode"]
    return provider["baseUrl"].rstrip("/") + "/chat/completions", provider["apiKey"]


def format_yi(value):
    if value is None:
        return ""
    return f"{value / 100000000:.2f}亿"


def format_pct(value):
    if value is None:
        return ""
    return f"{value:.2f}%"


def extract_snapshot(extra_raw):
    if not extra_raw:
        return ""
    try:
        obj = json.loads(extra_raw)
    except Exception:
        return ""
    if not isinstance(obj, dict):
        return ""

    latest_indicator = (obj.get("fina_indicator") or [{}])[0]
    latest_income = (obj.get("income") or [{}])[0]
    latest_balance = (obj.get("balancesheet") or [{}])[0]
    latest_disclosure = obj.get("latest_disclosure") or {}
    latest_biz = obj.get("fina_mainbz") or []
    latest_holder = obj.get("holdertrade") or []

    main_biz_lines = []
    latest_biz_date = ""
    for item in latest_biz[:5]:
        biz_date = item.get("end_date") or latest_biz_date
        latest_biz_date = latest_biz_date or biz_date
        name = item.get("bz_item") or "未命名业务"
        sales = format_yi(item.get("bz_sales"))
        margin = ""
        if item.get("bz_sales") and item.get("bz_profit") is not None:
            margin_value = item["bz_profit"] / item["bz_sales"] * 100
            margin = f"，业务毛利率约 {margin_value:.1f}%"
        main_biz_lines.append(f"- {name}：营收 {sales}{margin}")

    holder_lines = []
    for item in latest_holder[:5]:
        direction = "增持" if item.get("in_de") == "IN" else "减持"
        holder_lines.append(
            f"- {item.get('ann_date', '')}｜{item.get('holder_name', '')}｜{direction}｜{int(item.get('change_vol') or 0)}股"
        )

    lines = [
        "你必须优先使用下面这组最新口径数据；正文凡是提到最新季报/最新口径，必须严格与这些数字一致：",
        f"- 最新报告期：{latest_income.get('end_date', '')}",
        f"- 最新营收：{format_yi(latest_income.get('revenue'))}",
        f"- 最新净利润：{format_yi(latest_income.get('n_income'))}",
        f"- 最新毛利率：{format_pct(latest_indicator.get('grossprofit_margin'))}",
        f"- 最新 ROE：{format_pct(latest_indicator.get('roe'))}",
        f"- 最新资产负债率：{format_pct(latest_indicator.get('debt_to_assets'))}",
        f"- 最新经营现金流/利润比：{format_pct(latest_indicator.get('ocf_to_profit'))}",
        f"- 最新总资产：{format_yi(latest_balance.get('total_assets'))}",
        f"- 最新总负债：{format_yi(latest_balance.get('total_liab'))}",
    ]

    if main_biz_lines:
        lines.append(f"- 最新主营构成（{latest_biz_date or '最近披露期'}）：")
        lines.extend(main_biz_lines)
    if holder_lines:
        lines.append("- 近期股东变动方向（IN=增持，DE=减持，方向不能写反）：")
        lines.extend(holder_lines)

    disclosure_priority_lines = []
    if latest_disclosure:
        source_type = latest_disclosure.get("source_type") or "latest_disclosure"
        source_title = latest_disclosure.get("source_title") or source_type
        source_level = latest_disclosure.get("source_level") or "unknown"
        report_period = latest_disclosure.get("report_period") or ""
        disclosure_date = latest_disclosure.get("disclosure_date") or ""
        disclosure_metrics = latest_disclosure.get("metrics") or {}
        disclosure_lines = [
            "- 以下是最新公告披露补丁，若与 Tushare 旧口径冲突，优先用这份披露：",
            f"  - 来源：{source_title} | 数据等级：{source_level} | 报告期：{report_period} | 披露日：{disclosure_date}",
        ]
        revenue_info = disclosure_metrics.get("revenue") or {}
        if revenue_info.get("value") is not None:
            revenue_yoy = revenue_info.get("yoy")
            yoy_text = f"（同比 {revenue_yoy:+.1f}%）" if revenue_yoy is not None else ""
            disclosure_lines.append(f"  - 最新披露营收：{format_yi(revenue_info.get('value'))}{yoy_text}")
        net_profit_info = disclosure_metrics.get("net_profit") or {}
        if net_profit_info.get("value") is not None:
            profit_yoy = net_profit_info.get("yoy")
            yoy_text = f"（同比 {profit_yoy:+.1f}%）" if profit_yoy is not None else ""
            disclosure_lines.append(f"  - 最新披露净利润：{format_yi(net_profit_info.get('value'))}{yoy_text}")
        elif net_profit_info.get("min") is not None and net_profit_info.get("max") is not None:
            yoy_min = net_profit_info.get("yoy_min")
            yoy_max = net_profit_info.get("yoy_max")
            yoy_text = ""
            if yoy_min is not None and yoy_max is not None:
                yoy_text = f"（同比 {yoy_min:+.1f}% ~ {yoy_max:+.1f}%）"
            disclosure_lines.append(
                f"  - 最新披露净利润预计区间：{format_yi(net_profit_info.get('min'))} ~ {format_yi(net_profit_info.get('max'))}{yoy_text}"
            )
        if latest_disclosure.get("summary"):
            disclosure_lines.append(f"  - 披露摘要：{latest_disclosure.get('summary')}")
        if latest_disclosure.get("change_reason"):
            disclosure_lines.append(f"  - 变动原因：{latest_disclosure.get('change_reason')}")
        if latest_disclosure.get("is_estimate"):
            disclosure_lines.append("  - 这是一份业绩预告/预测口径：正文必须写“预计/预告/区间”，禁止伪装成正式年报已披露。")
            disclosure_priority_lines.extend([
                "- 写作优先级要求：文章开头必须先点出这份最新业绩预告/快报，再回头解释旧季报或旧年报背景。",
                "- 如果最新公告与旧季报并存，叙事主轴应围绕最新公告展开，旧季报只能作为对照背景，不得喧宾夺主。",
                "- 财务段必须明确区分“最新季报实际值”和“全年预告区间”，不能只写旧季报而弱化最新披露。",
            ])
        elif source_level == "formal_report":
            disclosure_priority_lines.extend([
                "- 写作优先级要求：如果数据等级是 formal_report，文章开头和财务段必须优先围绕这份正式披露展开，不要让旧季报成为主叙事。",
            ])
        elif source_level == "periodic_report":
            disclosure_priority_lines.extend([
                "- 写作优先级要求：如果数据等级是 periodic_report，正文的主要财务结论必须先写这份定期报告，再拿旧年报做背景对照。",
            ])
        else:
            disclosure_priority_lines.extend([
                "- 写作优先级要求：如果已经有最新披露，文章开头和财务段必须优先围绕这份最新披露展开，不要让旧季报成为主叙事。",
            ])
        lines.extend(disclosure_lines)
    if disclosure_priority_lines:
        lines.extend(disclosure_priority_lines)
    lines.append("- 如果某个数字在旧年报和最新季报之间不一致，优先使用上面的最新口径。")
    return "\n".join(lines)


def common_rules():
    return (
        "硬约束：\n"
        "- 只引用已提供数据；没有数据就不要编造\n"
        "- 必须解释主营业务、增长驱动、毛利率/ROE/资产负债率/现金流、3-4个风险\n"
        "- 最新季报口径优先于旧年报口径，涉及营收、净利润、毛利率、ROE、资产负债率时不得混用旧数\n"
        "- 如果最新数据来自业绩预告或业绩快报，正文必须明确写出“业绩预告/业绩快报/预计/区间”，不能把预告口径写成正式年报结论\n"
        "- 如果存在最新公告披露补丁，文章开头和财务段必须优先写这份最新披露，旧季报/旧年报只能退居背景或对照\n"
        "- 不允许出现“明明已有最新预告/快报，却仍把去年三季度或旧年报写成主叙事”的情况\n"
        "- 禁止投资建议、禁止目标价、禁止正文手写免责声明\n"
        "- 结尾自然收束即可，可抛互动问题或留下开放性判断，不要写死任何固定结束语。\n"
        "- 必须保留且仅保留这四个占位符：\n"
        "[插入配图：公司/工厂]\n"
        "[插入配图：核心产品/业务]\n"
        "[插入配图：财务数据]\n"
        "[插入配图：结尾]\n"
    )


def anti_ai_rules():
    return (
        "去模板化写作要求：\n"
        "- 不要把文章写成标准答案，不要每一段都像同一个模子里刻出来。\n"
        "- 不要强行做“反差金句”“三段排比”“这不是……而是……”这类高识别句式。\n"
        "- 少用“值得注意的是”“更重要的是”“说白了”“某种程度上”“从A到B”“真正的问题是”这类连接拐杖。\n"
        "- 句子长度必须有明显变化：短句、长句、半停顿句都可以，不要整篇一个节奏。\n"
        "- 允许段落长短不齐，允许某些段落只讲一个点，不要每段都按“结论+解释+拔高”完整收口。\n"
        "- 少用破折号，能用逗号、句号、括号解决就不要用破折号。\n"
        "- 少用整齐的编号列表，除非信息天然是列表。大部分地方优先写成自然段。\n"
        "- 不要硬凹“故事感”或“截图金句”，重点是像一个真的长期写财经号的人在说人话。\n"
        "- 不要为了显得有情绪而夸张，不要使用震惊体、宿命感、宏大隐喻。\n"
        "- 如果一个判断已经由数据说明白了，就直接说，不要再做二次拔高。\n"
        "- 允许保留一点作者视角，但前提是克制、具体、贴着事实，不要抒情泛滥。\n"
    )

def structure_rules():
    return (
        "内容结构规范 v3：\n"
        "- 全文采用：1个开头钩子 + 5个主段 + 1个结尾收束。\n"
        "- 开头前三段必须完成三件事：抛事实反差、补充关键矛盾、明确本文要回答的问题。\n"
        "- 开头前200字必须出现公司名、行业词、核心数据或核心现象，不能一上来写成立时间、注册地、实控人。\n"
        "- 第一主段不是资料卡，而是讲清公司定位、市场为什么关注它、这次最值得拆的矛盾。\n"
        "- 第二主段必须把业务逻辑翻译成人话：它真正卖什么、怎么赚钱、护城河是长期能力还是阶段性红利。\n"
        "- 第三主段必须做财务穿透：至少覆盖营收、净利润、毛利率、ROE、资产负债率、现金流中的4项，并且每个关键数字后都尽量补一句‘这意味着什么’。\n"
        "- 第四主段必须写出竞争格局和行业位置的对比感，不能只写‘行业空间大、竞争激烈’。\n"
        "- 第五主段必须把风险写成具体场景，并明确后续真正值得跟踪的公开信号。\n"
        "- 风险段允许引入更深入的公开风险信号：控股股东质押或减持、控制权变化、ST或退市风险警示、立案调查、交易所问询、重大诉讼或被执行、商票逾期、评级下调、重大审计意见、停产减产、主流财经媒体集中负面舆情。\n"
        "- 一旦识别到退市、私有化、债务违约、评级下调、质押冻结、控制权变化、重大诉讼、监管处罚等重大风险，必须顺着因果链往下写，不能只停留在现象层。\n"
        "- 风险下钻时至少回答4个问题：结果事实是什么、直接原因是什么、中层原因是什么、更深层机制是什么；素材足够时再补‘这个机制今天还在不在’。\n"
        "- 风险溯源必须区分证据等级：公告和交易所文件属于A级，可直接写；主流财经媒体属于B级，要写成公开报道；市场讨论或地方舆情属于C级，只能写成一种公开解释，不能写成公司已确认结论。\n"
        "- 风险段最终要让读者看见的是一条风险放大链条，而不是几个并列标签。\n"
        "- 风险链的内部推理可以按‘异常结果、直接原因、中层原因、更深层机制、回到今天’组织，但正文默认不要把这些词直接打成标签。\n"
        "- 除非用户明确要求报告体，否则不要出现‘异常结果：’‘直接原因：’‘中层原因：’这类答题卡式写法。\n"
        "- 更好的正文写法是：一个真正的小标题 + 2到4个自然段推进，让证据、判断、转折自然嵌进句子里。\n"
        "- 可参照风险溯源示例的写法：先写异常结果，再写直接原因，再写中层原因，再写更深层机制，最后回到这条机制今天还在不在。\n"
        "- 如果命中重大风险，风险段内部最好形成一个完整小节，按‘异常结果 -> 直接原因 -> 中层原因 -> 更深层机制 -> 回到今天’的顺序推进。\n"
        "- 退市、私有化、纾困、债务展期、违约、评级下调、ST、立案调查等信号，默认属于重大风险自动触发场景。\n"
        "- 如果命中单个强风险信号，或命中两个以上中强度风险信号，或形成链式风险组合，必须直接切换到风险溯源模式。\n"
        "- 尤其是‘退市 -> 私有化 -> 纾困 -> 债务展期或违约’这类链条，不允许只写表面结果，必须继续追问为什么会走到这一步。\n"
        "- 如果涉及港股退市、私有化、纾困或长期停牌场景，必须优先依赖 HKEXnews、港交所规则与指引、SFC、公司通函和评级材料，而不是先从泛媒体开始拼故事。\n"
        "- 港股场景检索时，不要只搜‘退市、私有化、纾困’，还要联动搜索撤销上市地位、长期停牌、复牌指引、协议安排、要约、债务重组、评级下调、债权人安排计划、清盘呈请、中国信达等关键词。\n"
        "- 时间轴检索应默认覆盖过去5年，并在必要时继续向前延伸；不要因为示例年份出现过2019、2020、2021，就把时间窗口固定死。\n"
        "- 如果重大风险链条的起点明显早于过去5年，或过去5年只能看到结果看不到原因，就必须继续向前追，直到因果链闭合。\n"
        "- 时间轴扩展的目标不是写更长的公司史，而是解释清楚‘今天这件事是从什么时候开始累积的’。\n"
        "- 不管 A 股还是港股，风险检索都不要只搜‘风险、负面、问题’，还应系统扩展到质押、冻结、控制权变更、立案调查、问询函、诉讼、被执行、违约、展期、商票逾期、审计意见、持续经营等高价值风险词。\n"
        "- 港股场景下，先确认上市状态和公告时间线，再确认私有化或纾困安排，再确认违约、展期、评级或债务压力，最后才用媒体和市场讨论补解释。\n"
        "- 像地方互保、隐性担保、市场传闻这类更深层解释，若没有 A/B 级来源支撑，只能写成一种市场公开解释，不能写成公司确认事实。\n"
        "- 这些风险信号只有在公开可验证、且与投资判断直接相关时才允许写入。\n"
        "- 默认不要把招聘信息、员工讨薪、员工匿名评价、脉脉或BOSS爆料写进正文，除非它们已经被公告、交易所文件或主流财经媒体正式确认。\n"
        "- 结尾必须完成三件事：一句话给出最终判断、点出后续变量、抛出有讨论价值的互动问题。\n"
        "- 版式必须像公众号成稿：主标题控制在5个左右，段落尽量短，连续6行以上的大段要主动拆开。\n"
        "- 小标题不要用一二三四编号，必须像判断句或提问句，并尽量自然嵌入公司名、行业词、核心概念词。\n"
        "- 必须保留并合理安放4个图片占位符，让图片成为阅读中继点，而不是装饰。\n"
        "- 每个主段结尾最好有一句收束，让读者知道‘这一段真正说明了什么’。\n"
        "- **必须包含企业发展时间轴+行业地位段落**：至少给出3个关键时间节点（成立、上市、重组、产能扩张等），给出行业排名/地位变迁，用具体数字给出规模锚点，段末补一句定性判断。\n"
        "- 企业发展段至少产出1句可截图判断句，例如：**'它不是行业里最大的，但可能是最专注的。'**\n"
        "- **必须包含股东价值分级分析**：先判断股东等级，优质股东（央企/上市公司/国家队/顶级机构）重点写信用背书和资源赋能；问题股东（债务违约/被执行/高质押/负面舆情）重点写风险传导和隔离；普通股东一笔带过即可。股东段至少产出1句可截图判断句。\n"
        "- 每600到800字至少出现1句可截图判断句，但金句必须有数据或事实支撑，不能只是情绪爽句。\n"
        "- 所有金句判断句必须用Markdown **加粗标记**包裹，例如：**这就是这家公司的核心矛盾所在。**，确保在公众号中黑体高亮显示，方便读者截图传播。\n"
        "- 全文至少完成3次专业概念翻译成人话，至少完成2次对比之后得出的结论，至少出现1次‘市场看法 vs 真实经营’的拆分。\n"
        "- 目标不是版式花哨，也不是内容堆砌，而是既像成熟公众号成稿，又像资深作者在做真正的研究拆解。\n"
    )


def style_goals():
    return (
        "版式和内容目标：\n"
        "- 版式参考优秀公众号成稿：好读、好扫读、图片节奏自然、段落推进清楚。\n"
        "- 内容深度和金句质量至少不低于当前最佳深度样稿，尤其要强化解释力、财务穿透和结论收束。\n"
        "- 不要写成资料卡，不要把产品清单、行业地位、战略方向机械平铺。\n"
        "- 不要为了金句而金句，真正可截图的句子应该是压缩后的研究判断。\n"
        "- 不要只有故事感没有研究，也不要只有研究感没有公众号阅读节奏。\n"
    )


def build_generate_prompt(company, extra="", risk_brief=""):
    snapshot = extract_snapshot(extra)
    prompt = f"你是成熟的财经公众号写作者，请为「{company}」输出一篇中文 Markdown 深度分析文章。\n\n"
    prompt += common_rules()
    prompt += "\n"
    prompt += anti_ai_rules()
    prompt += "\n"
    prompt += structure_rules()
    prompt += "\n"
    prompt += style_goals()
    prompt += "\n写作执行要求：\n"
    prompt += "- 标题要符合‘搜索锚点 + 点击钩子’双优化，必须出现公司名，最好带1个数字或1个反差。\n"
    prompt += "- 正文默认采用5个主标题左右，不要写成单块长文，也不要滑回14板块八股体。\n"
    prompt += "- 以自然段为主，可以少量使用 bullet，但不要把全文写成清单。\n"
    prompt += "- 业务段重点回答‘它到底怎么赚钱’，财务段重点回答‘这些数字到底说明什么’，风险段重点回答‘后续该盯什么’。\n"
    prompt += "- 风险段优先写股东、监管、退市、诉讼、商票、审计、重大舆情这些高相关信号，不要写招聘、讨薪、员工评价这类不适合公众号正文的素材。\n"
    prompt += "- 风险链不要写成粗体标签排队出现的报告腔，优先写成自然段推进。\n"
    prompt += "- 如果命中重大风险，优先把这一段写成一个完整的小节，而不是几句并列判断。\n"
    prompt += "- 如果识别到退市、私有化、纾困、债务展期、违约、评级下调、ST、立案调查等关键词，不要犹豫，直接进入重大风险溯源模式。\n"
    prompt += "- 如果是港股退市、私有化、纾困场景，优先按港交所公告、SFC、公司文件、评级材料的顺序核实，再使用媒体解释。\n"
    prompt += "- 港股场景的检索词不要只停留在退市、私有化、纾困，还应扩展到协议安排、要约、复牌指引、债务重组、评级下调、AMC 参与等词组。\n"
    prompt += "- 时间轴默认按当前年份往前回看5年；如果重大风险链条更早但仍然影响今天，再继续向前追，不要把年份写死成2019、2020、2021。\n"
    prompt += "- 如果近5年只能看到最后结果、看不到形成过程，要继续往前扩时间轴，直到写清风险链起点。\n"
    prompt += "- 通用风险检索时，也要主动补质押、冻结、控制权、问询、处罚、诉讼、被执行、商票、审计意见、持续经营这些词，不要只写财务和行业风险。\n"
    prompt += "- 至少产出3处有解释力的判断，至少1处带证据支撑的财务金句。\n"
    prompt += "- 结尾不要礼貌收尾，要完成观点收束。\n"
    if snapshot:
        prompt += "\n以下是必须优先遵守的最新财务快照：\n" + snapshot + "\n"
    if extra:
        prompt += "\n以下是可引用的完整财务和风控数据：\n" + extra + "\n"
    if risk_brief:
        prompt += "\n以下是结构化风险底稿（优先用于风险段写作与风险溯源）：\n" + risk_brief + "\n"
    prompt += "\n请直接输出完整 Markdown 正文，不要解释过程。"
    return prompt


def build_rewrite_prompt(company, draft, review, extra="", risk_brief=""):
    snapshot = extract_snapshot(extra)
    prompt = f"你是成熟的财经公众号写作者，现在要根据审稿意见返修文章。\n\n公司：{company}\n\n"
    prompt += common_rules()
    prompt += "\n"
    prompt += anti_ai_rules()
    prompt += "\n"
    prompt += structure_rules()
    prompt += "\n"
    prompt += style_goals()
    prompt += "返修任务要求：\n"
    prompt += "- review.md 里的每一条修改意见都必须落实。\n"
    prompt += "- 先修正所有不通过的数据项，再修边界表达和合规问题，最后再优化版式和节奏。\n"
    prompt += "- 如果 review 指出最新营收、净利润、毛利率等数字不一致，必须以最新财务快照为准重写相关句子。\n"
    prompt += "- 如果 review 点名某个原文片段，必须优先在原位置修正，不要只在别处补一句新数据。\n"
    prompt += "- 如果 review 指出业绩预告区间不通过，必须明确写出预计净利润X亿至Y亿，并加上预告、预计、显示等边界词。\n"
    prompt += "- 三季报实际值和全年预告区间不能写在同一个 markdown 表格行中，必须分段呈现。\n"
    prompt += "- 返修时不要滑回旧版八股体，也不要为了过审把文章修成资料卡。\n"
    prompt += "- 如果原稿结构松散、段落过长、图片占位符位置不合理，可以在不改变事实的前提下重组段落，让它更符合内容结构规范 v3。\n"
    prompt += "- 如果原稿金句太空或结尾太弱，可以增强判断句和结尾收束，但不能新增无依据结论。\n"
    prompt += "- 如果需要补强风险段，优先补公开可验证的股东、监管、诉讼、商票、退市、重大舆情信号；不要补招聘、讨薪、匿名员工评价。\n"
    prompt += "- 如果原稿已经写到重大风险事件，但只停留在表面现象，应继续补写直接原因、中层原因和更深层机制，并按证据等级保留边界。\n"
    prompt += "- 风险改写时可参考风险溯源示例：不要把‘退市、私有化、违约、问询’写成孤立事件，要写清它们之间的前后关系。\n"
    prompt += "- 风险改写时也可参考风险溯源小节模板：尽量让这一段形成完整的段落推进，而不是堆几个风险句子。\n"
    prompt += "- 如果原稿里已经出现‘异常结果：/直接原因：/中层原因：/更深层机制：’这类标签，请优先改写成自然段，而不是继续保留。\n"
    prompt += "- 如果集团债务、互保、担保链条主要来自 B/C 级证据，正文必须写成‘公开报道显示’或‘市场曾有一种解释’，不要写成已被公司正式确认的结论。\n"
    prompt += "- 如果原稿已经出现退市、私有化、纾困、债务展期、违约、评级下调等关键词，却没有下钻原因链，要把它视为结构性遗漏并补齐。\n"
    prompt += "- 如果原稿涉及港股退市或私有化，但来源层级混乱，应先按港交所公告和正式文件重排事实，再补媒体和市场层解释。\n"
    prompt += "- 如果原稿对港股风险只写了退市或私有化结果，没有把协议安排、要约、债务展期、评级下调、AMC 纾困等关键词链条补全，也要继续补写。\n"
    prompt += "- 如果原稿时间轴停在近几年结果，但没有交代更早的起点和累积过程，也要视情况把时间轴往前拉。\n"
    prompt += "- 如果原稿风险段只写了行业竞争、利润波动、原材料价格，却没有补控制权、监管、诉讼、信用、审计这类结构性风险，也要视情况补强。\n"
    if snapshot:
        prompt += "\n以下是必须优先遵守的最新财务快照：\n" + snapshot + "\n"
    if extra:
        prompt += "\n以下是可引用的完整财务和风控数据：\n" + extra + "\n"
    if risk_brief:
        prompt += "\n以下是结构化风险底稿（优先用于风险段写作与风险溯源）：\n" + risk_brief + "\n"
    prompt += "\n当前草稿：\n" + draft + "\n\nreview.md：\n" + review + "\n\n"
    prompt += "请根据 review.md 逐条修正，输出新的完整 Markdown 正文，不要解释过程。"
    return prompt


def build_humanize_prompt(company, draft, review, extra="", risk_brief=""):
    snapshot = extract_snapshot(extra)
    prompt = f"你现在是财经编辑，不是写稿机器人。请把下面这篇关于「{company}」的稿子做一次深度去模板化重写。\n\n"
    prompt += common_rules()
    prompt += "\n"
    prompt += anti_ai_rules()
    prompt += "\n"
    prompt += structure_rules()
    prompt += "\n"
    prompt += style_goals()
    prompt += "去模板化任务要求：\n"
    prompt += "- 保留所有关键事实、数字、判断边界和图片占位符。\n"
    prompt += "- 不改变文章核心观点，不新增任何数据，不删掉重要风险点。\n"
    prompt += "- 重点处理‘像 AI 写的’骨架：固定起承转合、过度钩子、整齐排比、套路收束、满篇金句、每段一个标准结论。\n"
    prompt += "- 优先改骨架和节奏，其次再改词。不要只是把几个连接词换一下。\n"
    prompt += "- 如果某一段已经很自然，就少动；如果一段明显像模板，就大胆改写句式和段落组织。\n"
    prompt += "- 人性化重写后，文章仍然要符合内容结构规范 v3，不能去掉公众号节奏、主段推进和结尾收束。\n"
    prompt += "- 最终效果要像一个长期写财经内容的人，基于真实数据写出来的自然稿。\n"
    if review:
        prompt += "- 下面附带最近一轮 review.md。即使去模板化，也不能回退这些已修正的事实边界。\n"
    if snapshot:
        prompt += "\n以下是必须优先遵守的最新财务快照：\n" + snapshot + "\n"
    if extra:
        prompt += "\n以下是可引用的完整财务和风控数据：\n" + extra + "\n"
    if risk_brief:
        prompt += "\n以下是结构化风险底稿（优先用于风险段写作与风险溯源）：\n" + risk_brief + "\n"
    prompt += "\n当前稿件：\n" + draft + "\n"
    if review:
        prompt += "\n最近一轮 review.md：\n" + review + "\n"
    prompt += "\n请直接输出完整 Markdown 正文，不要解释过程。"
    return prompt


def call_model(prompt, temperature):
    url, api_key = load_provider()
    last_err = None
    for model in MODELS:
        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 3500,
                "temperature": temperature,
            }
        ).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                if content:
                    return content
                last_err = f"{model}: empty content {json.dumps(data, ensure_ascii=False)[:400]}"
        except Exception as e:
            body = getattr(e, "read", lambda: b"")().decode("utf-8", "ignore") if hasattr(e, "read") else ""
            last_err = f"{model}: {e} {body[:400]}"
    raise RuntimeError(last_err or "no available qwencode model")


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: bailian_writer.py <generate|rewrite|humanize> <company> [draft_file] [review_file] [extra_file] [risk_brief_file]")

    def safe_arg(index: int) -> str:
        if len(sys.argv) <= index:
            return ""
        return str(sys.argv[index] or "").strip()

    def read_if_file(value: str) -> str:
        if not value:
            return ""
        path = pathlib.Path(value)
        if not path.exists() or path.is_dir():
            return ""
        return path.read_text()

    mode = sys.argv[1]
    company = sys.argv[2]
    extra = read_if_file(safe_arg(5))
    risk_brief = read_if_file(safe_arg(6))

    if mode == "generate":
        print(call_model(build_generate_prompt(company, extra, risk_brief), 0.7))
        return
    if mode == "rewrite":
        draft = pathlib.Path(safe_arg(3)).read_text()
        review = read_if_file(safe_arg(4))
        print(call_model(build_rewrite_prompt(company, draft, review, extra, risk_brief), 0.45))
        return
    if mode == "humanize":
        draft = pathlib.Path(safe_arg(3)).read_text()
        review = read_if_file(safe_arg(4))
        print(call_model(build_humanize_prompt(company, draft, review, extra, risk_brief), 0.85))
        return
    raise SystemExit("unknown mode")


if __name__ == "__main__":
    main()
