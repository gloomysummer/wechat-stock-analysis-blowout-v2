#!/usr/bin/env node
/**
 * 自动搜索获取真实数据 + 生成文章 + 强制审稿 + 自动返修 + HTML
 */

const https = require('https');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { config, validateConfig } = require('./config');
const { rewriteArticle } = require('./call_qwencode');

const libPath = path.join(__dirname, '..', '..', '..', 'lib', 'tavily_pool.js');
const { get_routed_key, mark_key_success, mark_key_error } = require(libPath);
const MAX_REWRITE_ROUNDS = 3;

function searchTavily(query) {
  return new Promise((resolve) => {
    const apiKey = get_routed_key({ route: 'china_hk_finance', query });
    if (!apiKey) {
      console.error('无可用 Tavily API key');
      resolve([]);
      return;
    }

    const postData = JSON.stringify({ query, api_key: apiKey, max_results: 3 });
    const options = {
      hostname: 'api.tavily.com',
      path: '/search',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData),
      },
    };
    const req = https.request(options, (res) => {
      let body = '';
      res.on('data', (chunk) => (body += chunk));
      res.on('end', () => {
        try {
          const result = JSON.parse(body);
          if (res.statusCode === 200) {
            mark_key_success(apiKey);
            resolve(result.results || []);
          } else {
            mark_key_error(apiKey, `HTTP ${res.statusCode}`);
            resolve([]);
          }
        } catch (e) {
          mark_key_error(apiKey, `Parse error: ${e.message}`);
          resolve([]);
        }
      });
    });
    req.on('error', (error) => {
      mark_key_error(apiKey, error.message);
      resolve([]);
    });
    req.write(postData);
    req.end();
  });
}

async function searchCompany(companyName) {
  console.log(`🔍 搜索: ${companyName}`);
  const queries = [
    `${companyName} 2024年 财报 营收 利润 同比增长`,
    `${companyName} 2024年 年报 业绩`,
    `雪球 ${companyName} 股票 分析 投资`,
    `财联社 ${companyName}`,
    `东方财富 ${companyName} 研报 分析`,
    `${companyName} 竞争对手 行业格局`,
    `${companyName} 核心业务 产品`,
    `${companyName} 发展历史 里程碑`,
  ];

  let results = [];
  for (const q of queries) {
    console.log(`  搜索: ${q.substring(0, 30)}...`);
    const r = await searchTavily(q);
    results = results.concat(r);
  }
  console.log(`  找到 ${results.length} 条\n`);
  return results;
}

function callLLM(systemPrompt, userPrompt) {
  return new Promise((resolve, reject) => {
    const requestBody = {
      model: config.minimax.model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      max_tokens: 10000,
      temperature: 0.4,
    };
    const url = new URL(config.minimax.baseUrl);
    const options = {
      hostname: url.hostname,
      path: '/v1/text/chatcompletion_v2',
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.minimax.apiKey}`,
        'Content-Type': 'application/json',
      },
    };
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try {
          resolve(JSON.parse(data).choices[0].message.content);
        } catch (e) {
          reject(new Error(`LLM 响应解析失败: ${data.substring(0, 200)}`));
        }
      });
    });
    req.on('error', reject);
    req.write(JSON.stringify(requestBody));
    req.end();
  });
}

async function generateArticle(companyName, searchResults) {
  console.log('📝 生成文章...');
  const context = searchResults.slice(0, 15).map((r) => `- ${r.title}: ${(r.content || '').substring(0, 300)}`).join('\n');

  const prompt = `你是一个深谙微信公众号爆款写法的财经作者。模仿「光影资本Pro」公众号风格，撰写关于「${companyName}」的故事化深度分析文章。

要求：
1. 开头前三句必须用数据反差、反常识或悬念抓住眼球。
2. 不要使用“第N期”“一、公司简介”“二、行业分析”这类八股结构。必须写成 4-6 个故事化主章节，小标题要像判断句或提问句。
3. 所有具体数字、年份、审计、股东、客户、估值等事实，优先且尽量仅使用参考信息中的已知数据；如果参考信息没有，就不要编造具体数字或具体对象。
4. 每个关键数据后都要翻译成人话，兼顾亮点、风险和未来推演。
5. 负面表述必须降级，严禁投资建议，严禁军事化措辞。
6. 不要在 Markdown 正文中手动写免责声明或“投资需谨慎”类尾注。
7. 结尾保留互动问题，以自然互动问题收束。

参考信息：
${context}

直接输出完整 Markdown。`;

  return callLLM(prompt, '请撰写一篇详尽的专业分析文章');
}

function generateHTML(markdown) {
  const html = markdown
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n- /g, '<br>• ');

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>上市公司分析</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px auto; max-width: 800px; }
h1 { font-size: 24px; text-align: center; margin-bottom: 30px; }
h2 { font-size: 18px; margin-top: 25px; border-left: 4px solid #1a73e8; padding-left: 10px; }
h3 { font-size: 16px; margin-top: 20px; }
p { font-size: 14px; line-height: 1.8; color: #333; }
strong { color: #1a73e8; }
table { border-collapse: collapse; width: 100%; margin: 15px 0; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 12px; }
th { background: #f5f5f5; }
img { max-width: 100%; height: auto; margin: 10px 0; }
</style>
</head>
<body>
<p>${html}</p>
</body>
</html>`;
}

function runDeterministicReview(outputDir, companyName) {
  const articlePath = path.join(outputDir, 'article.md');
  const dataPath = path.join(outputDir, 'financial_data.json');
  if (!fs.existsSync(dataPath)) {
    throw new Error(`缺少 ${dataPath}，无法执行强制审稿。请先运行 financial_analysis.py`);
  }
  const reviewPath = path.join(outputDir, 'review.md');
  let passed = true;
  try {
    execFileSync('python3', [path.join(__dirname, 'review_article.py'), articlePath, dataPath, reviewPath, companyName], {
      stdio: 'inherit',
    });
  } catch (err) {
    passed = false;
  }
  const reviewText = fs.existsSync(reviewPath) ? fs.readFileSync(reviewPath, 'utf8') : '';
  return { passed, reviewPath, reviewText };
}

async function main(companyName) {
  try {
    validateConfig(['minimax.apiKey']);
    const searchResults = await searchCompany(companyName);
    const outputDir = config.paths.outputDir;
    fs.mkdirSync(outputDir, { recursive: true });
    const articlePath = path.join(outputDir, 'article.md');

    let article = await generateArticle(companyName, searchResults);
    fs.writeFileSync(articlePath, article);
    console.log(`✅ Markdown: ${articlePath}`);

    for (let round = 0; round <= MAX_REWRITE_ROUNDS; round += 1) {
      const review = runDeterministicReview(outputDir, companyName);
      if (review.passed) {
        const html = generateHTML(fs.readFileSync(articlePath, 'utf8'));
        const htmlPath = `${outputDir}/index.html`;
        fs.writeFileSync(htmlPath, html);
        console.log(`✅ HTML: ${htmlPath}`);
        console.log(`✅ 审稿: ${review.reviewPath}`);
        console.log(`\n🌐 文章已生成到 ${outputDir}`);
        console.log('\n' + fs.readFileSync(articlePath, 'utf8'));
        return;
      }

      if (round === MAX_REWRITE_ROUNDS) {
        throw new Error(`审稿已连续失败 ${MAX_REWRITE_ROUNDS + 1} 轮，最后一份 review: ${review.reviewPath}`);
      }

      console.log(`✍️ 第 ${round + 1} 轮返修中...`);
      article = await rewriteArticle(companyName, fs.readFileSync(articlePath, 'utf8'), review.reviewText);
      fs.writeFileSync(articlePath, article);
      console.log(`🛠️ 已根据 review.md 回写第 ${round + 1} 版 article.md`);
    }
  } catch (err) {
    console.error('❌', err.message);
    process.exit(1);
  }
}

if (require.main === module) {
  main(process.argv.slice(2).join(' ') || '广东新宝电器股份有限公司');
}

module.exports = { main };
