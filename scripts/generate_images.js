#!/usr/bin/env node
/**
 * 配图生成脚本
 * 使用 ModelScope API 生成文章配图
 *
 * 用法: node generate_images.js <prompt> [output_path]
 * Token 从环境变量 MODELSCOPE_TOKEN 读取
 */

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

// 禁用代理 - ModelScope API 国内直连
[
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'ALL_PROXY',
  'http_proxy',
  'https_proxy',
  'all_proxy',
  'GLOBAL_AGENT_HTTP_PROXY',
  'GLOBAL_AGENT_HTTPS_PROXY',
  'global_agent_http_proxy',
  'global_agent_https_proxy',
].forEach((key) => {
  delete process.env[key];
});
process.env.NO_PROXY = '*';
process.env.no_proxy = '*';

const DIRECT_HTTPS_AGENT = new https.Agent({ keepAlive: true, maxSockets: 8 });
const DIRECT_HTTP_AGENT = new http.Agent({ keepAlive: true, maxSockets: 8 });
const REQUEST_TIMEOUT_MS = 30000;

const { config, validateConfig } = require('./config');
const PEXELS_API_KEY_PATH = '/home/ubuntu/.config/openclaw/pexels_api_key';
const PEXELS_SKILL_SCRIPT = '/home/ubuntu/.openclaw/workspace/skills/pexels-image-downloader/download.js';

function requestTimeoutError(phase, timeoutMs = REQUEST_TIMEOUT_MS) {
  const error = new Error(`${phase} timed out after ${timeoutMs}ms`);
  error.code = 'ETIMEDOUT';
  return error;
}

function requestJson(optionsReq, body, phase) {
  return new Promise((resolve, reject) => {
    const req = https.request(
      {
        ...optionsReq,
        agent: DIRECT_HTTPS_AGENT,
      },
      (res) => {
        let responseBody = '';
        res.on('data', (chunk) => (responseBody += chunk));
        res.on('end', () => {
          try {
            const parsed = JSON.parse(responseBody);
            resolve({ statusCode: res.statusCode || 0, body: parsed, rawBody: responseBody });
          } catch (error) {
            reject(new Error(`${phase}: invalid JSON response: ${responseBody.slice(0, 200)}`));
          }
        });
      },
    );

    req.setTimeout(REQUEST_TIMEOUT_MS, () => {
      req.destroy(requestTimeoutError(phase));
    });
    req.on('error', (error) => {
      reject(new Error(`${phase}: ${error.message}`));
    });

    if (body) {
      req.write(body);
    }
    req.end();
  });
}

function loadPexelsApiKey() {
  if (process.env.PEXELS_API_KEY) return process.env.PEXELS_API_KEY.trim();
  try {
    if (fs.existsSync(PEXELS_API_KEY_PATH)) {
      return fs.readFileSync(PEXELS_API_KEY_PATH, 'utf8').trim();
    }
  } catch (err) {}
  return '';
}

function inferPexelsQuery(prompt, filename = '') {
  const text = `${filename} ${prompt}`.toLowerCase();
  if (filename.includes('003') || /financial|dashboard|capital|profit|trend|metrics|finance/.test(text)) {
    if (/semiconductor|chip|wafer|server|optical/.test(text)) return 'server room';
    if (/robot|automation|factory|industrial/.test(text)) return 'industrial control room';
    if (/pharma|medical|biotech|lab/.test(text)) return 'laboratory analysis';
    return 'financial office';
  }
  if (filename.includes('004') || /reflective|uncertainty|ending|closing|corridor/.test(text)) {
    if (/pharma|medical|biotech|lab/.test(text)) return 'laboratory corridor';
    if (/robot|automation|factory|industrial/.test(text)) return 'empty factory';
    if (/semiconductor|chip|wafer/.test(text)) return 'cleanroom corridor';
    return 'office building';
  }
  if (/semiconductor|chip|wafer|cleanroom|server/.test(text)) return 'semiconductor factory';
  if (/robot|robotic|automation|actuator|servo/.test(text)) return 'robotic arm factory';
  if (/pharma|medical|biotech|drug|clinical|laboratory/.test(text)) return 'biotech laboratory';
  if (/automotive|vehicle|chassis|thermal/.test(text)) return 'car factory';
  if (/optical|fiber|network|module/.test(text)) return 'data center';
  if (/manufacturing|industrial|equipment|production line|workshop/.test(text)) return 'industrial factory';
  return 'industrial factory';
}

async function searchPexelsPhoto(apiKey, query) {
  if (!fs.existsSync(PEXELS_SKILL_SCRIPT)) {
    throw new Error(`Pexels skill script not found: ${PEXELS_SKILL_SCRIPT}`);
  }
  const stdout = execFileSync('node', [PEXELS_SKILL_SCRIPT, query, '1', '1'], {
    encoding: 'utf8',
    env: { ...process.env, PEXELS_API_KEY: apiKey },
    maxBuffer: 10 * 1024 * 1024,
  }).trim();
  let results;
  try {
    results = JSON.parse(stdout);
  } catch (err) {
    throw new Error(`Pexels skill returned invalid JSON: ${stdout.slice(0, 200)}`);
  }
  if (!Array.isArray(results) || results.length === 0) {
    throw new Error(`Pexels skill returned no photos for query: ${query}`);
  }
  const photo = results[0];
  return photo.src?.large || photo.src?.original || photo.src?.medium || '';
}

async function generateImageWithPexelsFallback(prompt, filename) {
  const apiKey = loadPexelsApiKey();
  if (!apiKey) {
    throw new Error('PEXELS_API_KEY not available');
  }
  const query = inferPexelsQuery(prompt, filename);
  const imageUrl = await searchPexelsPhoto(apiKey, query);
  if (!imageUrl) {
    throw new Error(`Pexels image URL missing for query: ${query}`);
  }
  return { buffer: await downloadImage(imageUrl), query, imageUrl };
}

/**
 * 生成单张图片
 * @param {string} apiToken - ModelScope Access Token
 * @param {string} prompt - 图片描述
 * @param {object} options - 可选参数
 * @returns {Promise<Buffer>} - 图片 Buffer
 */
async function generateImage(apiToken, prompt, options = {}) {
  const model = options.model || config.modelscope.model;
  const size = options.size || '1024x1024';
  const seed = options.seed || Math.floor(Math.random() * 100000);

  const taskId = await submitTask(apiToken, model, prompt, { size, seed });
  return await waitForResult(apiToken, taskId);
}

/**
 * 并行生成多张图片
 * @param {string} apiToken - ModelScope Access Token
 * @param {Array} prompts - 图片描述数组 [{prompt, filename}]
 * @param {string} outputDir - 输出目录
 * @returns {Promise<Object>} - 结果数组
 */
async function generateImagesParallel(apiToken, prompts, outputDir) {
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  console.log(`🚀 并行生成 ${prompts.length} 张图片...\n`);

  const tasks = prompts.map(async (item, index) => {
    const { prompt, filename } = item;
    const outputPath = `${outputDir}/${filename}`;

    console.log(`  [${index + 1}/${prompts.length}] 提交: ${filename}`);

    try {
      const imageBuffer = await generateImage(apiToken, prompt);
      fs.writeFileSync(outputPath, imageBuffer);
      console.log(`    ✅ 完成: ${filename}`);
      return { filename, success: true, provider: 'modelscope' };
    } catch (err) {
      console.log(`    ⚠️ ModelScope 失败: ${filename} - ${err.message}`);
      try {
        const fallback = await generateImageWithPexelsFallback(prompt, filename);
        fs.writeFileSync(outputPath, fallback.buffer);
        console.log(`    ✅ Pexels 兜底成功: ${filename} | query=${fallback.query}`);
        return {
          filename,
          success: true,
          provider: 'pexels',
          fallbackQuery: fallback.query,
          fallbackImageUrl: fallback.imageUrl,
          modelscopeError: err.message,
        };
      } catch (fallbackErr) {
        console.log(`    ❌ Pexels 兜底失败: ${filename} - ${fallbackErr.message}`);
        return {
          filename,
          success: false,
          error: `ModelScope: ${err.message} | Pexels: ${fallbackErr.message}`,
        };
      }
    }
  });

  const results = await Promise.all(tasks);
  const successCount = results.filter((r) => r.success).length;
  console.log(`\n🎉 完成！成功 ${successCount}/${prompts.length}`);
  return results;
}

/**
 * 提交生成任务
 */
function submitTask(apiToken, model, prompt, options) {
  const data = JSON.stringify(
    {
      model,
      prompt,
      size: options.size || '1024x1024',
      seed: options.seed || Math.floor(Math.random() * 100000),
    },
    null,
    2,
  );

  return requestJson(
    {
      hostname: 'api-inference.modelscope.cn',
      path: '/v1/images/generations',
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiToken}`,
        'Content-Type': 'application/json',
        'X-ModelScope-Async-Mode': 'true',
      },
    },
    data,
    'submit task',
  ).then(({ body, rawBody }) => {
    if (body.task_id) {
      return body.task_id;
    }
    throw new Error(`Submit failed: ${rawBody.substring(0, 200)}`);
  });
}

/**
 * 轮询等待任务完成
 */
function waitForResult(apiToken, taskId, maxRetries = 80) {
  return new Promise((resolve, reject) => {
    let retries = 0;

    const check = () => {
      if (retries > maxRetries) {
        reject(new Error('Timeout waiting for image generation'));
        return;
      }

      requestJson(
        {
          hostname: 'api-inference.modelscope.cn',
          path: `/v1/tasks/${taskId}`,
          method: 'GET',
          headers: {
            Authorization: `Bearer ${apiToken}`,
            'X-ModelScope-Task-Type': 'image_generation',
          },
        },
        null,
        `poll task ${taskId}`,
      )
        .then(({ body, rawBody }) => {
          if (body.task_status === 'SUCCEED') {
            const imageUrl = body.output_images?.[0];
            if (!imageUrl) {
              reject(new Error(`Image generation succeeded but output_images is empty: ${rawBody.substring(0, 200)}`));
              return;
            }
            downloadImage(imageUrl).then(resolve).catch(reject);
          } else if (body.task_status === 'FAILED') {
            reject(new Error(`Image generation failed: ${rawBody.substring(0, 200)}`));
          } else {
            retries++;
            setTimeout(check, 15000); // 80 * 15s = 20 minutes max polling window
          }
        })
        .catch(reject);
    };

    check();
  });
}

/**
 * 下载图片
 */
function downloadImage(url, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    if (redirectCount > 5) {
      reject(new Error(`download image: too many redirects for ${url}`));
      return;
    }

    const client = url.startsWith('https') ? https : http;
    const agent = url.startsWith('https') ? DIRECT_HTTPS_AGENT : DIRECT_HTTP_AGENT;
    const req = client.get(url, { agent }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        // 处理重定向
        downloadImage(res.headers.location, redirectCount + 1).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`download image: unexpected status ${res.statusCode} for ${url}`));
        return;
      }
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        resolve(Buffer.concat(chunks));
      });
      res.on('error', reject);
    });
    req.setTimeout(REQUEST_TIMEOUT_MS, () => {
      req.destroy(requestTimeoutError(`download image ${url}`));
    });
    req.on('error', (error) => {
      reject(new Error(`download image: ${error.message}`));
    });
  });
}

// CLI 入口
if (require.main === module) {
  const args = process.argv.slice(2);
  if (args.length < 1) {
    console.log('用法: node generate_images.js <prompt> [output_path]');
    console.log('  Token 从环境变量 MODELSCOPE_TOKEN 读取');
    console.log('示例: node generate_images.js "A modern office building" ./test.png');
    process.exit(1);
  }

  const [prompt, outputPath] = args;

  console.log('正在生成图片...');

  const runner = config.modelscope.token
    ? generateImage(config.modelscope.token, prompt).then((buffer) => ({ buffer, provider: 'modelscope' }))
    : generateImageWithPexelsFallback(prompt, path.basename(outputPath || 'output.png')).then((result) => ({
        buffer: result.buffer,
        provider: 'pexels',
      }));

  runner
    .then(({ buffer, provider }) => {
      const outPath = outputPath || 'output.png';
      fs.writeFileSync(outPath, buffer);
      console.log(`✅ 已保存: ${outPath} (${buffer.length} bytes) via ${provider}`);
    })
    .catch((err) => {
      console.error('❌ 失败:', err.message);
      process.exit(1);
    });
}

module.exports = { generateImage, generateImagesParallel };
