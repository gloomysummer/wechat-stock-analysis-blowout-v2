#!/usr/bin/env node
/**
 * 写稿模型调用
 * 优先复用 OpenClaw 当前实际 agent provider；仅在缺失时回退到旧的 百炼 配置。
 */

const https = require('https');
const fs = require('fs');
const { config } = require('./config');

function buildBasePrompt(companyName, extraData = '') {
  return `你是成熟的财经公众号写作者，请为「${companyName}」输出一篇中文 Markdown 爆款分析文章。

硬约束：
- 开头前三句必须抓人，用反差、疑问或关键数据开局
- 不要写“一、二、三”式八股标题，要用有判断的短标题
- 只引用已提供数据；没有数据就不要编造
- 必须解释主营业务、增长驱动、毛利率/ROE/资产负债率/现金流、3-4个风险
- 禁止投资建议、禁止目标价、禁止正文手写免责声明
- 结尾自然收束即可，可抛互动问题或留下开放性判断。
- 必须保留且仅保留这四个占位符：
[插入配图：公司/工厂]
[插入配图：核心产品/业务]
[插入配图：财务数据]
[插入配图：结尾]

${extraData ? '以下是可引用的财务和风控数据：\n' + extraData : ''}`;
}

async function generateDetailedArticle(companyName, extraData = '') {
  const systemPrompt = buildBasePrompt(companyName, extraData) + '\n\n直接输出 Markdown 格式的爆款文章全文。';
  const userPrompt = `请为“${companyName}”撰写这篇爆款分析文章。`;
  return callWriter(systemPrompt, userPrompt);
}

async function rewriteDetailedArticle(companyName, draftArticle, reviewFeedback, extraData = '') {
  const systemPrompt = buildBasePrompt(companyName, extraData) + `

你现在处于“审稿返修”阶段。你必须严格根据 review.md 逐条修改草稿：
- 每一条“不通过”都必须改掉
- 每一条“待人工复核”不需要乱改事实，但要补足边界表达
- 如果 review 明确要求删掉免责声明、修正数字、修正 IN/DE 增减持方向，必须照做
- 不允许解释，不允许辩解，不允许输出审稿意见，只输出修订后的完整 Markdown 正文
- 不允许漏掉四个配图占位符
- 只要 review 没说要删掉的观点，不要无端大改文章结构`;

  const userPrompt = `公司：${companyName}

当前草稿如下：

${draftArticle}

--- review.md ---
${reviewFeedback}

请根据上面的 review.md 逐条修正，输出新的完整 Markdown。`;
  return callWriter(systemPrompt, userPrompt);
}

function readOpenClawConfig() {
  try {
    return JSON.parse(fs.readFileSync('/home/ubuntu/.openclaw/openclaw.json', 'utf8'));
  } catch (err) {
    return {};
  }
}

function getRuntimeConfig() {
  const openclawConfig = readOpenClawConfig();
  const providers = openclawConfig?.models?.providers || {};
  const defaults = openclawConfig?.agents?.defaults?.model || {};
  const operatorModel = (openclawConfig?.agents?.list || []).find((agent) => agent.id === 'operator')?.model || '';
  const candidates = [
    defaults.primary,
    ...(Array.isArray(defaults.fallbacks) ? defaults.fallbacks : []),
    operatorModel,
    `qwencode/${config.minimax.model}`,
  ].filter(Boolean);

  for (const candidate of candidates) {
    const [providerName, ...rest] = candidate.split('/');
    const model = rest.join('/');
    const provider = providers[providerName];
    if (!provider?.apiKey || !provider?.baseUrl) continue;
    return {
      providerName,
      model: model || provider.model || config.minimax.model,
      apiKey: provider.apiKey,
      baseUrl: provider.baseUrl,
      api: provider.api || (providerName: 'minimax'' ? 'openai-completions' : ''),
    };
  }

  if (config.minimax.apiKey && config.minimax.baseUrl) {
    return {
      providerName: 'minimax'',
      model: config.minimax.model,
      apiKey: config.minimax.apiKey,
      baseUrl: config.minimax.baseUrl,
      api: 'anthropic-messages',
    };
  }

  return null;
}

function parseAnthropicText(result) {
  if (!result || !Array.isArray(result.content)) return '';
  return result.content
    .filter((item) => item && item.type === 'text' && item.text)
    .map((item) => item.text)
    .join('\n')
    .trim();
}

function callWriter(systemPrompt, userPrompt) {
  return new Promise((resolve, reject) => {
    const runtime = getRuntimeConfig();
    if (!runtime?.apiKey || !runtime?.baseUrl || !runtime?.model) {
      reject(new Error('未找到可用的写稿模型配置（qwencode/qwencode）'));
      return;
    }

    const url = new URL(runtime.baseUrl);
    const useAnthropic = runtime.api === 'anthropic-messages' || /\/anthropic\/?$/.test(runtime.baseUrl);
    const useOpenAI = runtime.api === 'openai-completions';
    const openAIUserPrompt = `${systemPrompt}\n\n## 用户任务\n${userPrompt}\n\n请直接输出完整 Markdown 正文，不要解释过程。`;
    const requestBody = useAnthropic
      ? {
          model: runtime.model,
          system: systemPrompt,
          messages: [{ role: 'user', content: userPrompt }],
          max_tokens: 8000,
          temperature: 0.4,
        }
      : {
          model: runtime.model,
          messages: useOpenAI
            ? [{ role: 'user', content: openAIUserPrompt }]
            : [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: userPrompt },
              ],
          max_tokens: useOpenAI ? 3000 : 8000,
          temperature: useOpenAI ? 0.7 : 0.4,
        };

    const options = {
      hostname: url.hostname,
      path: useAnthropic
        ? `${url.pathname.replace(/\/$/, '')}/v1/messages`
        : useOpenAI
          ? `${url.pathname.replace(/\/$/, '')}/chat/completions`
          : '/v1/text/chatcompletion_v2',
      method: 'POST',
      headers: useAnthropic
        ? {
            'x-api-key': runtime.apiKey,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
          }
        : {
            Authorization: `Bearer ${runtime.apiKey}`,
            'Content-Type': 'application/json',
          },
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          if (useAnthropic) {
            const content = parseAnthropicText(result);
            if (content) {
              resolve(content);
              return;
            }
            reject(new Error(`Anthropic API Error (${runtime.providerName}/${runtime.model}): ${data.substring(0, 300)}`));
            return;
          }
          const content = result?.choices?.[0]?.message?.content;
          if (content) {
            resolve(content);
          } else if (result.base_resp && result.base_resp.status_code !== 0) {
            reject(new Error(`API Error (${runtime.providerName}/${runtime.model}): ${result.base_resp.status_msg}`));
          } else {
            reject(new Error(`Unknown error (${runtime.providerName}/${runtime.model}): ${data.substring(0, 300)}`));
          }
        } catch (e) {
          reject(new Error(`响应解析失败 (${runtime.providerName}/${runtime.model}): ${data.substring(0, 300)}`));
        }
      });
    });

    req.on('error', reject);
    req.write(JSON.stringify(requestBody));
    req.end();
  });
}

if (require.main === module) {
  const companyName = process.argv[2] || '宁德时代';
  console.log(`📝 正在生成: ${companyName}...\n`);
  generateDetailedArticle(companyName)
    .then((article) => {
      console.log(article);
    })
    .catch((err) => {
      console.error('❌:', err.message);
      process.exit(1);
    });
}

module.exports = {
  generateArticle: generateDetailedArticle,
  rewriteArticle: rewriteDetailedArticle,
};
