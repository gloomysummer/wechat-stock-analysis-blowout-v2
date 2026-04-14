#!/usr/bin/env node
/**
 * 公众号文章生成工具
 * 整合：撰写文章 + 强制审稿 + 自动返修 + 生成配图
 */

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { generateImagesParallel } = require('./generate_images');
const { config, validateConfig } = require('./config');
const WRITER_SCRIPT = path.join(__dirname, 'bailian_writer.py');
const RISK_BRIEF_SCRIPT = path.join(__dirname, 'generate_risk_brief.py');
const EXTERNAL_RISK_BRIEF_SCRIPT = path.join(__dirname, 'generate_external_risk_brief.py');
const COMPANY_PROFILE_BRIEF_SCRIPT = path.join(__dirname, 'generate_company_profile_brief.py');
const FINANCIAL_ANALYSIS_SCRIPT = path.join(__dirname, 'financial_analysis.py');

const MAX_REWRITE_ROUNDS = 3;
const RUN_LOCK_FILE = '.write_article.lock.json';

function isPidRunning(pid) {
  if (!pid || !Number.isInteger(pid)) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return false;
  }
}

function resolveRunLockPath(outputDir) {
  return path.join(outputDir, RUN_LOCK_FILE);
}

function acquireRunLock(outputDir, companyName) {
  const lockPath = resolveRunLockPath(outputDir);
  if (fs.existsSync(lockPath)) {
    try {
      const current = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
      if (isPidRunning(Number(current.pid))) {
        throw new Error(`检测到同一输出目录已有进行中的 write_article 任务（pid=${current.pid}，startedAt=${current.startedAt}），为避免并发覆盖，已拒绝重复执行。`);
      }
    } catch (err) {
      if (err.message.includes('检测到同一输出目录已有进行中的 write_article 任务')) {
        throw err;
      }
    }
    fs.rmSync(lockPath, { force: true });
  }

  const payload = {
    pid: process.pid,
    companyName,
    startedAt: new Date().toISOString(),
    outputDir,
  };
  fs.writeFileSync(lockPath, JSON.stringify(payload, null, 2), 'utf8');
  return lockPath;
}

function releaseRunLock(lockPath) {
  if (!lockPath) return;
  try {
    fs.rmSync(lockPath, { force: true });
  } catch (err) {}
}

function removeIfExists(targetPath) {
  if (!targetPath || !fs.existsSync(targetPath)) return;
  fs.rmSync(targetPath, { force: true, recursive: true });
}

function cleanupOutputArtifacts(outputDir) {
  const removable = [
    'article.md',
    'article.html',
    'index.html',
    'review.md',
    'generation_status.json',
    'company_profile_brief.md',
    'risk_brief.md',
    'external_risk_brief.md',
    'risk_seed_data.json',
  ];
  removable.forEach((name) => removeIfExists(path.join(outputDir, name)));

  for (const entry of fs.readdirSync(outputDir)) {
    if (/^.+_wechat_package\.zip$/i.test(entry) || /^dyg_.*\.(png|jpg|jpeg|webp)$/i.test(entry)) {
      removeIfExists(path.join(outputDir, entry));
    }
  }
}

function runPythonScriptToFileAtomic(scriptPath, buildArgs, finalPath, warningPrefix) {
  const tmpPath = `${finalPath}.tmp-${process.pid}-${Date.now()}`;
  try {
    execFileSync('python3', [scriptPath, ...buildArgs(tmpPath)], {
      stdio: 'inherit',
      maxBuffer: 20 * 1024 * 1024,
    });
    if (!fs.existsSync(tmpPath)) {
      throw new Error(`脚本未生成目标文件: ${tmpPath}`);
    }
    fs.renameSync(tmpPath, finalPath);
    return finalPath;
  } catch (err) {
    removeIfExists(tmpPath);
    console.warn(`${warningPrefix}${err.message}`);
    return '';
  }
}

function runWriter(mode, companyName, outputDir, reviewPath = '', riskBriefPath = '', extraPathOverride = '') {
  const articlePath = path.join(outputDir, 'article.md');
  const extraPath = extraPathOverride || resolveFinancialDataPath(outputDir);
  const args = mode === 'generate'
    ? [WRITER_SCRIPT, 'generate', companyName, '', '', extraPath, riskBriefPath]
    : mode === 'humanize'
      ? [WRITER_SCRIPT, 'humanize', companyName, articlePath, reviewPath, extraPath, riskBriefPath]
      : [WRITER_SCRIPT, 'rewrite', companyName, articlePath, reviewPath, extraPath, riskBriefPath];
  return execFileSync('python3', args, { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 }).trim();
}

function insertImagePlaceholders(article, imagesDir) {
  let result = article;
  result = result.replace(/\[插入配图：公司\/工厂\]/g, `\n![封面图](${imagesDir}/image_001.png)\n`);
  result = result.replace(/\[插入配图：核心产品\/业务\]/g, `\n![核心业务](${imagesDir}/image_002.png)\n`);
  result = result.replace(/\[插入配图：财务数据\]/g, `\n![财务数据](${imagesDir}/image_003.png)\n`);
  result = result.replace(/\[插入配图：结尾\]/g, `\n![结尾](${imagesDir}/image_004.png)\n`);
  return result;
}

const INDUSTRY_HINTS = [
  // 优先级调整：更具体的细分行业排在前面
  { regex: /(PCB|印制电路板)/, label: 'advanced manufacturing and industrial equipment', visual: 'precision pcb production lines, copper traces, cnc drilling, industrial automation workshop' },
  { regex: /(芯片|半导体|服务器|算力|GPU|AI芯片|晶圆|EDA|封测|刻蚀|MOCVD)/, label: 'semiconductors and AI computing infrastructure', visual: 'wafer fabrication cleanroom, etching chambers, chip packaging, server clusters, blue-lit semiconductor facility' },
  { regex: /(光模块|通信|CPO|交换机|算力网络|光芯片|高速互联)/, label: 'optical communication and AI networking', visual: 'optical modules, fiber links, high-speed switches, photonic components, futuristic network lab' },
  { regex: /(机器人|人形机器人|谐波减速器|伺服|自动化|机械臂|智能装备)/, label: 'robotics and industrial automation', visual: 'robotic arms, reducers, servo systems, smart factory, industrial automation cells' },
  { regex: /(消费电子|苹果|手机|耳机|面板|光学|摄像头)/, label: 'consumer electronics supply chain', visual: 'consumer electronics components, optics modules, precision assembly line, sleek device internals' },
  { regex: /(汽车|汽零|特斯拉|底盘|减震|热管理|执行器|座舱|电驱)/, label: 'automotive components and intelligent chassis', visual: 'electric vehicle chassis, thermal systems, actuators, automotive assembly, robotics around vehicle platform' },
  { regex: /(医药|创新药|制药|原料药|临床|器械|CXO|生物学|分子|药物|实验室|合成)/, label: 'pharmaceuticals and medical technology', visual: 'biotech laboratory, molecular structures, reactors, clean clinical workspace, pharmaceutical research equipment' },
  { regex: /(军工|航空|导弹|卫星|雷达|船舶)/, label: 'defense and aerospace manufacturing', visual: 'aerospace engineering, radar systems, advanced composites, industrial military factory' },
  { regex: /(高端制造|设备|机床|工控)/, label: 'advanced manufacturing and industrial equipment', visual: 'precision machinery, cnc lines, industrial controls, advanced manufacturing workshop' },
];

function safeReadText(filePath) {
  try {
    return fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8') : '';
  } catch (err) {
    return '';
  }
}

function safeReadJson(filePath) {
  try {
    if (!filePath || !fs.existsSync(filePath)) return null;
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (err) {
    return null;
  }
}

function firstMatch(text, regex, fallback = '') {
  const match = text.match(regex);
  return match ? match[1].trim() : fallback;
}

function compactText(text, maxLen = 220) {
  return (text || '').replace(/\s+/g, ' ').replace(/["“”]/g, '\\"').trim().slice(0, maxLen);
}

function detectIndustry(text) {
  for (const item of INDUSTRY_HINTS) {
    if (item.regex.test(text)) return item;
  }
  return {
    label: 'Chinese A-share industrial company research',
    visual: 'premium Chinese equity research illustration, modern industrial setting, strong business atmosphere',
  };
}

function buildVisualAnchors(ctx) {
  const name = `${ctx.companyName} ${ctx.title} ${ctx.businessSection} ${ctx.riskSection}`.toLowerCase();
  if (/(药明康德|医药|创新药|制药|原料药|临床|器械|cxo|生物|实验室|药物)/i.test(name)) {
    return {
      coverScene: 'biotech laboratory with reactors, molecular glassware, pharmaceutical research benches, clean sterile lighting',
      businessScene: 'drug discovery workflow, lab instruments, chemical synthesis, clinical research environment',
      financialScene: 'pharmaceutical dashboards, molecule motifs, lab screens, analytical finance composition',
      endingScene: 'quiet biotech research corridor, reflective laboratory atmosphere, clinical precision',
    };
  }
  if (/(中微公司|半导体|芯片|晶圆|刻蚀|mocvd|服务器|算力|gpu|eda|封测)/i.test(name)) {
    return {
      coverScene: 'semiconductor cleanroom, etching equipment, wafer tools, chip fabrication environment, blue precision lighting',
      businessScene: 'wafer fabrication chambers, chip manufacturing tools, process engineers, semiconductor production line',
      financialScene: 'chip industry dashboard, wafer patterns, semiconductor metrics, technical financial composition',
      endingScene: 'quiet fab corridor, chip tools in the distance, restrained high-tech industrial atmosphere',
    };
  }
  if (/(机器人|机械臂|谐波|减速器|自动化|智能装备)/i.test(name)) {
    return {
      coverScene: 'robotic arms and humanoid components in a smart factory, dynamic industrial composition',
      businessScene: 'reducers, actuators, assembly cells, robotics production workshop',
      financialScene: 'robotics dashboard, industrial schematics, automation metrics scene',
      endingScene: 'empty smart factory floor, restrained industrial future mood',
    };
  }
  return {
    coverScene: ctx.industryVisual || 'industry-specific production environment with strong material details',
    businessScene: ctx.industryVisual || 'industry-specific production environment with layered equipment',
    financialScene: `${ctx.industryVisual || 'industrial'} with abstract data visualization atmosphere`,
    endingScene: ctx.industryVisual || 'industry-specific reflective environment',
  };
}

function extractMetrics(articleText) {
  return {
    revenue: firstMatch(articleText, /\|\s*营收\s*\|\s*([^|]+)\|/),
    netProfit: firstMatch(articleText, /\|\s*净利润\s*\|\s*([^|]+)\|/),
    grossMargin: firstMatch(articleText, /\|\s*毛利率\s*\|\s*([^|]+)\|/),
    roe: firstMatch(articleText, /\|\s*ROE\s*\|\s*([^|]+)\|/),
    debtRatio: firstMatch(articleText, /\|\s*资产负债率\s*\|\s*([^|]+)\|/),
  };
}

function buildPromptContext(companyName, outputDir) {
  const articlePath = path.join(outputDir, 'article.md');
  const articleText = safeReadText(articlePath);
  const data = safeReadJson(resolveFinancialDataPath(outputDir)) || {};
  const combinedText = `${companyName}\n${articleText}\n${JSON.stringify(data).slice(0, 4000)}`;
  const industry = detectIndustry(combinedText);
  const title = compactText(firstMatch(articleText, /^#\s+(.+)$/m, `${companyName} 深度研究`), 120);
  const hook = compactText(firstMatch(articleText, /\*\*(.+?)\*\*/, ''), 180);
  const businessSection = compactText(firstMatch(articleText, /## .*?(?:核心业务|业务拼图|业务逻辑).*?\n([\s\S]{0,260})\n\n!/m, ''), 220);
  const riskSection = compactText(firstMatch(articleText, /## .*?(?:暗雷|风险|裂缝).*?\n([\s\S]{0,260})\n---/m, ''), 220);
  const metrics = extractMetrics(articleText);

  return {
    companyName,
    title,
    hook,
    businessSection,
    riskSection,
    industryLabel: industry.label,
    industryVisual: industry.visual,
    metrics,
  };
}

const STRICT_IMAGE_RULE = 'Image only. Absolutely no text, no typography, no Chinese characters, no English words, no letters, no numbers, no digits, no captions, no labels, no chart labels, no watermarks, no logos, no interface text.';
const COVER_NEGATIVE_RULE = 'Default to no people. Avoid generic businessman portrait, avoid suited executive facing camera, avoid corporate stock-photo look, avoid repeated protagonist archetype, avoid hero shot at desk, avoid a single male figure as the focal point, avoid office portrait clichés.';

function buildImagePrompts(companyName, outputDir) {
  const ctx = buildPromptContext(companyName, outputDir);
  const anchors = buildVisualAnchors(ctx);

  return {
    cover: `Premium editorial cover illustration for ${ctx.companyName}. Industry: ${ctx.industryLabel}. Scene anchor: ${anchors.coverScene}. The focal point must be the company's business scene itself: equipment, production line, laboratory, product structure, industrial materials or scientific environment. Prefer zero people. If people appear, they must be tiny supporting figures embedded in the scene, never the hero subject. No portrait composition. No face-forward protagonist. Mood: strategic tension, growth versus pressure, cinematic but industry-specific, premium magazine realism, strong environmental storytelling. ${COVER_NEGATIVE_RULE} ${STRICT_IMAGE_RULE}`,
    business: `Business illustration for ${ctx.companyName}. Scene anchor: ${anchors.businessScene}. Focus on the company's products, production workflow, equipment, labs or manufacturing links. Prefer objects, machinery, interfaces, product internals and process details over people. Prioritize concrete industry artifacts over generic office imagery. Layered composition, premium realism, strong material detail. ${STRICT_IMAGE_RULE}`,
    financial: `Financial illustration for ${ctx.companyName}. Scene anchor: ${anchors.financialScene}. Show trend contrast, profitability pressure, capital intensity, but keep the visuals rooted in the company's industry context rather than generic trading screens. Abstract data shapes are allowed, but no readable symbols. Premium research aesthetic. ${STRICT_IMAGE_RULE}`,
    ending: `Reflective closing illustration for ${ctx.companyName}. Scene anchor: ${anchors.endingScene}. Mood: strategic uncertainty, industry pressure, restrained confidence. Prefer environment-only composition, no generic executive portrait, no centered single person. Use the company's industrial or scientific environment as the emotional backdrop. Minimal but powerful composition. ${STRICT_IMAGE_RULE}`,
  };
}


function getTushareToken() {
  const envPaths = [
    path.join(__dirname, '..', '.env'),
    path.join(process.cwd(), '.env'),
  ];
  for (const envPath of envPaths) {
    try {
      if (!fs.existsSync(envPath)) continue;
      const lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
      for (const line of lines) {
        const m = line.match(/^TUSHARE_TOKEN=(.*)$/);
        if (m) {
          return String(m[1] || '').trim().replace(/^['"]|['"]$/g, '');
        }
      }
    } catch (_) {}
  }
  return process.env.TUSHARE_TOKEN || '';
}

function resolveAshareTsCode(companyName, explicitStockCode = '') {
  const code = String(explicitStockCode || '').trim().toUpperCase();
  // A股代码：6位数字（可能带.SH/.SZ后缀）
  if (/^\d{6}\.(SH|SZ)$/.test(code)) return code;
  if (/^\d{6}$/.test(code)) return code.startsWith('6') ? `${code}.SH` : `${code}.SZ`;
  // 港股代码：4-6位数字带.HK后缀
  if (/^\d{4,6}\.HK$/i.test(code)) return code;

  const token = getTushareToken();
  if (!token) return '';
  const resolver = `
import sys, re
import tushare as ts
import pandas as pd
company = sys.argv[1]
token = sys.argv[2]
pro = ts.pro_api(token)
rows = []
for status in ['L','D','P']:
    try:
        df = pro.stock_basic(exchange='', list_status=status, fields='ts_code,name')
        if df is not None and not df.empty:
            rows.append(df)
    except Exception:
        pass
if not rows:
    print('')
    raise SystemExit(0)
df = pd.concat(rows, ignore_index=True).drop_duplicates(subset=['ts_code'])
def norm(v):
    v = str(v or '').strip()
    if v.startswith('*ST'):
        v = v[3:]
    elif v.startswith('ST'):
        v = v[2:]
    for suffix in ('股份有限公司','集团股份有限公司','集团有限公司','有限公司','集团'):
        if v.endswith(suffix):
            v = v[:-len(suffix)]
            break
    return v.strip()
base = norm(company)
for _, row in df.iterrows():
    n = norm(row['name'])
    if n == base:
        print(str(row['ts_code']))
        break
else:
    print('')
`;
  try {
    return execFileSync('python3', ['-c', resolver, companyName, token], {
      encoding: 'utf8',
      maxBuffer: 20 * 1024 * 1024,
    }).trim();
  } catch (_) {
    return '';
  }
}

function ensureFinancialAnalysis(companyName, stockCode, outputDir) {
  const resolved = resolveAshareTsCode(companyName, stockCode);
  if (!resolved) {
    console.warn(`⚠️ 未能为 ${companyName} 解析到 A 股/港股代码，继续走兼容模式。`);
    return '';
  }
  console.log(`📊 正在生成结构化财务底稿: ${resolved}`);
  execFileSync('python3', [FINANCIAL_ANALYSIS_SCRIPT, resolved], {
    stdio: 'inherit',
    env: { ...process.env, OUTPUT_DIR: outputDir },
    maxBuffer: 50 * 1024 * 1024,
  });
  return resolved;
}

function parseCliArgs(argv) {
  const args = argv.slice(2);
  const result = { companyName: '', stockCode: '', outputDir: '' };
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if ((arg === '--stock-code' || arg === '--code') && args[i + 1]) {
      result.stockCode = args[++i];
    } else if (arg === '--output-dir' && args[i + 1]) {
      result.outputDir = args[++i];
    } else if (!result.companyName) {
      result.companyName = arg;
    } else if (!result.stockCode && /^\d{4,6}(?:\.(?:SH|SZ|HK))?$/i.test(arg)) {
      result.stockCode = arg;
    } else if (!result.outputDir) {
      result.outputDir = arg;
    }
  }
  return result;
}

function clearExistingImages(imagesDir) {
  if (!fs.existsSync(imagesDir)) return;
  for (const entry of fs.readdirSync(imagesDir)) {
    if (/^image_\d{3}\.(png|jpg|jpeg|webp)$/i.test(entry)) {
      fs.rmSync(path.join(imagesDir, entry), { force: true });
    }
  }
}

function summarizeImageFailures(imageResults) {
  return imageResults
    .filter((item) => item && item.success === false)
    .map((item) => `${item.filename}: ${item.error}`)
    .join('; ');
}

function generateRiskBrief(companyName, outputDir) {
  const dataPath = resolveFinancialDataPath(outputDir);
  if (!dataPath) {
    console.warn('⚠️ 未找到 financial_data.json，跳过内部风险底稿生成。');
    return '';
  }

  const riskBriefPath = path.join(outputDir, 'risk_brief.md');
  return runPythonScriptToFileAtomic(
    RISK_BRIEF_SCRIPT,
    (tmpPath) => [dataPath, tmpPath, companyName],
    riskBriefPath,
    '⚠️ 内部风险底稿生成失败：',
  );
}

function generateCompanyProfileBrief(companyName, outputDir) {
  const profileBriefPath = path.join(outputDir, 'company_profile_brief.md');
  return runPythonScriptToFileAtomic(
    COMPANY_PROFILE_BRIEF_SCRIPT,
    (tmpPath) => [companyName, tmpPath],
    profileBriefPath,
    '⚠️ 公司概况底稿生成失败：',
  );
}

function ensureRiskSeedData(companyName, outputDir) {
  const dataPath = resolveFinancialDataPath(outputDir);
  if (dataPath) return dataPath;

  const seedPath = path.join(outputDir, 'risk_seed_data.json');
  const seed = {
    stock_basic: [
      {
        ts_code: '',
        name: companyName,
        list_date: '',
        market: '',
        area: '',
        industry: '',
        cnspell: '',
      },
    ],
    latest_disclosure: {},
  };
  fs.writeFileSync(seedPath, JSON.stringify(seed, null, 2), 'utf8');
  return seedPath;
}

function generateExternalRiskBrief(companyName, outputDir, companyProfileBriefPath = '') {
  const seedDataPath = ensureRiskSeedData(companyName, outputDir);
  if (!seedDataPath) {
    return '';
  }

  const externalRiskBriefPath = path.join(outputDir, 'external_risk_brief.md');
  return runPythonScriptToFileAtomic(
    EXTERNAL_RISK_BRIEF_SCRIPT,
    (tmpPath) => [seedDataPath, tmpPath, companyName, companyProfileBriefPath],
    externalRiskBriefPath,
    '⚠️ 外部风险底稿生成失败，后续仅使用其他底稿：',
  );
}

function mergeRiskBriefs(riskBriefPath, externalRiskBriefPath) {
  if (!riskBriefPath || !fs.existsSync(riskBriefPath)) {
    return externalRiskBriefPath && fs.existsSync(externalRiskBriefPath) ? externalRiskBriefPath : '';
  }
  if (!externalRiskBriefPath || !fs.existsSync(externalRiskBriefPath)) return riskBriefPath;

  const internalText = fs.readFileSync(riskBriefPath, 'utf8').trim();
  const externalText = fs.readFileSync(externalRiskBriefPath, 'utf8').trim();
  if (!externalText) return riskBriefPath;

  fs.writeFileSync(
    riskBriefPath,
    `${internalText}

---

${externalText}
`,
    'utf8'
  );
  return riskBriefPath;
}

function resolveWriterExtraPath(outputDir, companyProfileBriefPath = '') {
  const dataPath = resolveFinancialDataPath(outputDir);
  return dataPath || companyProfileBriefPath || '';
}

function hasStructuredFinancialData(outputDir) {
  return Boolean(resolveFinancialDataPath(outputDir));
}

function resolveFinancialDataPath(outputDir) {
  const directPath = path.join(outputDir, 'financial_data.json');
  if (fs.existsSync(directPath)) return directPath;

  const normalizedOutputDir = path.resolve(outputDir);
  const normalizedDefault = path.resolve(config.paths.outputDir);
  if (normalizedOutputDir === normalizedDefault) {
    const defaultPath = path.join(__dirname, '..', 'output', 'financial_data.json');
    if (fs.existsSync(defaultPath)) return defaultPath;
  }
  return '';
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function applyDeterministicReviewFixes(articleContent, reviewText) {
  if (!reviewText) return articleContent;
  let next = articleContent;

  // ── Fix 1: simple numeric mismatches ──
  const pattern = /正文 `([^`]+)`，源数据 `([0-9.]+)(亿|%)`/g;
  const numericPattern = /-?\d+(?:\.\d+)?\s*(?:亿|%)/;

  for (const match of reviewText.matchAll(pattern)) {
    const originalSnippet = match[1];
    const targetValue = `${match[2]}${match[3]}`;
    if (!numericPattern.test(originalSnippet)) continue;

    const replacementSnippet = originalSnippet.replace(numericPattern, targetValue);
    if (next.includes(originalSnippet)) {
      next = next.replaceAll(originalSnippet, replacementSnippet);
      continue;
    }

    const loosePrefix = originalSnippet.replace(numericPattern, '').trim();
    if (!loosePrefix) continue;
    const loosePattern = new RegExp(escapeRegExp(loosePrefix) + '[-0-9.亿%约至上下左右\\s]{0,24}');
    next = next.replace(loosePattern, (segment) => segment.replace(numericPattern, targetValue));
  }

  // ── Fix 2: forecast profit range ──
  const rangeSourceMatch = reviewText.match(/业绩预告区间\s*`(-?\d+(?:\.\d+)?)亿\s*~\s*(-?\d+(?:\.\d+)?)亿`/);
  if (rangeSourceMatch) {
    const rMin = rangeSourceMatch[1];
    const rMax = rangeSourceMatch[2];
    const correctRange = `预计净利润${rMin}亿至${rMax}亿`;
    const hasCorrectRange = new RegExp(`预[计告].*净利润.*${escapeRegExp(rMin)}.*亿.*(?:至|到|~).*${escapeRegExp(rMax)}.*亿`).test(next);
    if (!hasCorrectRange) {
      const profitMention = next.match(/(预[计告].*?净利润[^\n。]*?-?\d+(?:\.\d+)?亿[^\n。]*)/);
      if (profitMention) {
        next = next.replace(profitMention[0], `业绩预告显示，${correctRange}`);
      }
    }
  }

  return next;
}

function runDeterministicReview(articlePath, outputDir, companyName) {
  const dataPath = resolveFinancialDataPath(outputDir);
  if (!dataPath) {
    throw new Error('缺少 financial_data.json，无法执行强制审稿。请先运行 financial_analysis.py');
  }

  const reviewPath = path.join(outputDir, 'review.md');
  const reviewScript = path.join(__dirname, 'review_article.py');
  console.log('🔎 正在执行强制审稿...');

  let passed = true;
  try {
    execFileSync('python3', [reviewScript, articlePath, dataPath, reviewPath, companyName], { stdio: 'inherit' });
  } catch (err) {
    // exit code 2 = 审稿不通过（正常流程）；其他 exit code 或无 review.md = 脚本崩溃
    if (err.status === 2) {
      passed = false;
    } else {
      // 脚本异常崩溃，抛出真实错误而非静默当作审稿失败
      throw new Error(`review_article.py 执行异常（exit ${err.status}）: ${err.message}`);
    }
  }

  const reviewText = fs.existsSync(reviewPath) ? fs.readFileSync(reviewPath, 'utf8') : '';
  if (!reviewText) {
    throw new Error('review.md 未生成，review_article.py 可能崩溃，请检查日志。');
  }
  return { passed, reviewPath, reviewText };
}


// generateWithReviewLoop_afterDraft:
// 初稿已由调用方生成并写入 articlePath，本函数只负责审稿 + 返修循环。
// 审稿通过 → 返回结果；超过 MAX_REWRITE_ROUNDS 轮 → 转人工审稿分支。
async function generateWithReviewLoop_afterDraft(companyName, outputDir, articlePath, riskBriefPath = '', extraPathOverride = '') {
  for (let round = 0; round <= MAX_REWRITE_ROUNDS; round += 1) {
    const review = runDeterministicReview(articlePath, outputDir, companyName);
    if (review.passed) {
      console.log(`✅ 审稿通过: ${review.reviewPath}\n`);
      return {
        reviewPath: review.reviewPath,
        reviewPassed: true,
        rewriteRounds: round,
        manualReviewRequired: false,
      };
    }

    if (round === MAX_REWRITE_ROUNDS) {
      console.warn(`⚠️ 审稿已连续失败 ${MAX_REWRITE_ROUNDS + 1} 轮，转人工审稿分支: ${review.reviewPath}`);
      return {
        reviewPath: review.reviewPath,
        reviewPassed: false,
        rewriteRounds: MAX_REWRITE_ROUNDS + 1,
        manualReviewRequired: true,
      };
    }

    const rewriteRound = round + 1;
    console.log(`✍️ 第 ${rewriteRound} 轮返修中...`);
    const rewritten = applyDeterministicReviewFixes(
      runWriter('rewrite', companyName, outputDir, review.reviewPath, riskBriefPath, extraPathOverride),
      review.reviewText
    );
    require('fs').writeFileSync(articlePath, rewritten);
    console.log(`🛠️ 已根据 review.md 回写第 ${rewriteRound} 版 article.md\n`);
  }

  throw new Error('未能进入审稿状态');
}


function runHumanizerPass(companyName, outputDir, articlePath, reviewPath = '', riskBriefPath = '', extraPathOverride = '') {
  console.log('🫧 正在执行去模板化重写...');
  const humanized = applyDeterministicReviewFixes(
    insertImagePlaceholders(runWriter('humanize', companyName, outputDir, reviewPath, riskBriefPath, extraPathOverride), 'images'),
    reviewPath && fs.existsSync(reviewPath) ? fs.readFileSync(reviewPath, 'utf8') : ''
  );
  fs.writeFileSync(articlePath, humanized);

  let finalReview = runDeterministicReview(articlePath, outputDir, companyName);
  if (!finalReview.passed) {
    console.log('🩹 去模板化后出现事实/合规回退，执行一次定向修复...');
    const repaired = applyDeterministicReviewFixes(
      runWriter('rewrite', companyName, outputDir, finalReview.reviewPath, riskBriefPath, extraPathOverride),
      finalReview.reviewText
    );
    fs.writeFileSync(articlePath, repaired);
    finalReview = runDeterministicReview(articlePath, outputDir, companyName);
  }

  if (!finalReview.passed) {
    console.warn('⚠️ 去模板化后的最终稿仍未完全通过审稿，保留当前稿并继续人工复核。');
  } else {
    console.log('✅ 去模板化终稿已通过审稿。\n');
  }

  return {
    reviewPath: finalReview.reviewPath,
    reviewPassed: finalReview.passed,
  };
}

async function writeArticleWithImages(companyName, stockCode = '', outputDir = '') {
  const hasModelScope = Boolean(config.modelscope.token);
  const hasPexels = Boolean(process.env.PEXELS_API_KEY) || fs.existsSync('/root/.config/openclaw/pexels_api_key');
  if (!hasModelScope && !hasPexels) {
    throw new Error('缺少可用配图能力：未配置 MODELSCOPE_TOKEN，且未检测到 Pexels skill key。');
  }

  outputDir = outputDir || path.join(config.paths.outputDir, companyName);
  fs.mkdirSync(outputDir, { recursive: true });
  const lockPath = acquireRunLock(outputDir, companyName);
  const imagesDir = path.join(outputDir, 'images');
  fs.mkdirSync(imagesDir, { recursive: true });
  const generationStatus = {
    companyName,
    stockCode,
    outputDir,
    imagesDir,
    stage: 'starting',
    reviewPassed: false,
    manualReviewRequired: false,
    rewriteRounds: 0,
    generatedAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  persistGenerationStatus(outputDir, generationStatus);
  try {
    cleanupOutputArtifacts(outputDir);
    clearExistingImages(imagesDir);

    console.log(`📝 开始生成: ${companyName}`);
    ensureFinancialAnalysis(companyName, stockCode, outputDir);
    console.log(`📁 输出目录: ${outputDir}\n`);

    console.log('🧭 正在生成结构化风险底稿...');
    generationStatus.stage = 'brief-generation';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    const companyProfileBriefPath = generateCompanyProfileBrief(companyName, outputDir);
    let riskBriefPath = generateRiskBrief(companyName, outputDir);
    const externalRiskBriefPath = generateExternalRiskBrief(companyName, outputDir, companyProfileBriefPath);
    riskBriefPath = mergeRiskBriefs(riskBriefPath || externalRiskBriefPath, externalRiskBriefPath);
    const writerExtraPath = resolveWriterExtraPath(outputDir, companyProfileBriefPath);
    const canStructuredReview = hasStructuredFinancialData(outputDir);
    if (companyProfileBriefPath) {
      console.log(`✅ 公司概况底稿: ${companyProfileBriefPath}`);
    }
    if (riskBriefPath) {
      console.log(`✅ 风险底稿: ${riskBriefPath}`);
    }
    if (externalRiskBriefPath) {
      console.log(`✅ 外部风险底稿: ${externalRiskBriefPath}`);
    }
    console.log('');

    // ── Step 1: 生成初稿 ──────────────────────────────────────────
    const articlePath = path.join(outputDir, 'article.md');
    generationStatus.articlePath = articlePath;
    generationStatus.stage = 'draft-generation';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    console.log('🤖 正在调用百炼写稿模型生成文章...');
    const firstDraft = applyDeterministicReviewFixes(
      insertImagePlaceholders(runWriter('generate', companyName, outputDir, '', riskBriefPath, writerExtraPath), 'images'),
      ''
    );
    fs.writeFileSync(articlePath, firstDraft);
    console.log(`✅ 初稿已保存: ${articlePath}\n`);

    // ── Step 2: 初稿完成后立刻并行启动配图（基于初稿文本生成 prompt）────
    console.log('🎨 初稿已出，立即并行启动配图（不等审稿）...');
    generationStatus.stage = 'image-generation-started';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    const keyImages = [
      { key: 'cover', filename: 'image_001.png' },
      { key: 'business', filename: 'image_002.png' },
      { key: 'financial', filename: 'image_003.png' },
      { key: 'ending', filename: 'image_004.png' },
    ];
    const imagePrompts = buildImagePrompts(companyName, outputDir);
    const prompts = keyImages.map((img) => ({
      prompt: imagePrompts[img.key],
      filename: img.filename,
    }));

    const imageGenPromise = generateImagesParallel(config.modelscope.token, prompts, imagesDir);

    // ── Step 3: 审稿 + 返修循环（仅在有结构化财务数据时启用）────────
    generationStatus.stage = 'review-loop';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);

    let reviewResult;
    let reviewPath = '';
    let humanizedResult;
    if (canStructuredReview) {
      reviewResult = await generateWithReviewLoop_afterDraft(companyName, outputDir, articlePath, riskBriefPath, writerExtraPath);
      reviewPath = reviewResult.reviewPath;
      humanizedResult = runHumanizerPass(companyName, outputDir, articlePath, reviewPath, riskBriefPath, writerExtraPath);
    } else {
      console.warn('⚠️ 未检测到结构化财务数据，当前走港股/非Tushare兼容模式：跳过强制审稿器，先生成可读草稿，后续建议人工复核。');
      const humanized = insertImagePlaceholders(runWriter('humanize', companyName, outputDir, '', riskBriefPath, writerExtraPath), 'images');
      fs.writeFileSync(articlePath, humanized);
      reviewResult = { reviewPath: '', reviewPassed: false, rewriteRounds: 0, manualReviewRequired: true };
      humanizedResult = { reviewPath: '', reviewPassed: false };
    }

    // ── Step 5: 等待配图完成 ─────────────────────────────────────
    console.log('⏳ 等待配图任务完成...');
    generationStatus.stage = 'waiting-images';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    const imageResults = await imageGenPromise;
    generationStatus.imageResults = imageResults;
    generationStatus.stage = 'images-finished';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    const successCount = Array.isArray(imageResults) ? imageResults.filter((item) => item && item.success).length : 0;
    if (successCount < prompts.length) {
      throw new Error(`AI 配图失败：仅成功 ${successCount}/${prompts.length}。${summarizeImageFailures(imageResults)}`);
    }

    console.log('\n🎉 完成！');
    console.log(`   - 文章: ${path.join(outputDir, 'article.md')}`);
    console.log(`   - 审稿: ${reviewPath || '已跳过（兼容模式）'}`);
    console.log(`   - 配图: ${imagesDir}/`);

    Object.assign(generationStatus, {
      companyName,
      articlePath: path.join(outputDir, 'article.md'),
      htmlPath: path.join(outputDir, 'article.html'),
      reviewPath: humanizedResult.reviewPath || reviewPath,
      imagesDir,
      riskBriefPath,
      externalRiskBriefPath,
      reviewPassed: humanizedResult.reviewPassed,
      manualReviewRequired: reviewResult.manualReviewRequired || !humanizedResult.reviewPassed,
      rewriteRounds: reviewResult.rewriteRounds,
      humanized: true,
      generatedAt: new Date().toISOString(),
    });
    persistGenerationStatus(outputDir, generationStatus);

    console.log('\n📤 开始推送到公众号草稿箱...');
    generationStatus.stage = 'wechat-draft';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    try {
      const publishDraftPath = path.join(__dirname, 'publish_draft.js');
      execFileSync('node', [publishDraftPath, companyName, outputDir], {
        encoding: 'utf8',
        maxBuffer: 30 * 1024 * 1024,
        stdio: 'inherit',
      });
      generationStatus.pushedToWechatDraft = true;
      generationStatus.wechatDraftStatus = { ok: true };
      console.log('\n✅ 全部完成！文章已推送到微信公众号草稿箱，请登录查看并发布。');
    } catch (err) {
      console.warn('\n⚠️ 推送草稿箱失败: ' + err.message);
      console.warn('可手动执行: node scripts/publish_draft.js ' + companyName + ' ' + outputDir);
      generationStatus.pushedToWechatDraft = false;
      generationStatus.wechatDraftStatus = { ok: false, error: err.message };
    }

    console.log('\n📦 开始打包 ZIP 交付包...');
    generationStatus.stage = 'zip-package';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    try {
      const packageOutputPath = path.join(__dirname, 'package_output.js');
      const zipPath = path.join(outputDir, `${slugify(companyName)}_wechat_package.zip`);
      execFileSync('node', [packageOutputPath, companyName, '--output-dir', outputDir, '--zip-path', zipPath], {
        encoding: 'utf8',
        maxBuffer: 30 * 1024 * 1024,
        stdio: 'inherit',
      });
      generationStatus.zipPath = zipPath;
      generationStatus.deliveryPackageStatus = { ok: true, zipPath };
      console.log('\n✅ ZIP 打包完成，可直接发微信当前会话。');
      console.log('   message tool 示例: action=send, media=' + zipPath);
    } catch (err) {
      console.warn('\n⚠️ ZIP 打包失败: ' + err.message);
      generationStatus.deliveryPackageStatus = { ok: false, error: err.message };
    }

    generationStatus.stage = 'done';
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    return generationStatus;
  } catch (err) {
    generationStatus.stage = 'failed';
    generationStatus.error = err && err.message ? err.message : String(err);
    generationStatus.updatedAt = new Date().toISOString();
    persistGenerationStatus(outputDir, generationStatus);
    throw err;
  } finally {
    releaseRunLock(lockPath);
  }
}


function persistGenerationStatus(outputDir, payload) {
  if (!outputDir) return;
  try {
    fs.writeFileSync(path.join(outputDir, 'generation_status.json'), JSON.stringify(payload, null, 2));
  } catch (err) {
    console.warn(`⚠️ generation_status.json 写入失败: ${err.message}`);
  }
}

function slugify(input) {
  return String(input || '')
    .trim()
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, '_');
}

if (require.main === module) {
  const { companyName, stockCode, outputDir } = parseCliArgs(process.argv);
  if (!companyName) {
    console.log('用法: node write_article.js <公司名称> [股票代码] [输出目录]');
    console.log('或:   node write_article.js <公司名称> --stock-code 600811.SH --output-dir /abs/path');
    process.exit(1);
  }

  writeArticleWithImages(companyName, stockCode, outputDir).catch((err) => {
    console.error('❌ 失败:', err.message);
    process.exit(1);
  });
}

module.exports = { writeArticleWithImages, parseCliArgs, resolveAshareTsCode };
