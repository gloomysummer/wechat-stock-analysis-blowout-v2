#!/usr/bin/env node
const fs = require('fs');
const os = require('os');
const path = require('path');
const https = require('https');
const { execFileSync } = require('child_process');
const { config } = require('./config');
const { getReusableToken, readTokenCache, writeTokenCache } = require('./wechat_token_cache');

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {};
  for (let i = 0; i < args.length; i += 1) {
    if (!args[i].startsWith('--')) continue;
    const key = args[i].slice(2);
    const value = args[i + 1] && !args[i + 1].startsWith('--') ? args[++i] : 'true';
    opts[key] = value;
  }
  return opts;
}

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { resolve(data); }
      });
      res.on('error', reject);
    }).on('error', reject);
  });
}

function httpsPost(url, bodyBuf, contentType) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const req = https.request({
      hostname: urlObj.hostname,
      path: urlObj.pathname + urlObj.search,
      method: 'POST',
      headers: {
        'Content-Type': contentType,
        'Content-Length': bodyBuf.length,
      },
    }, (res) => {
      let resp = '';
      res.on('data', (c) => (resp += c));
      res.on('end', () => {
        try { resolve(JSON.parse(resp)); } catch (e) { resolve(resp); }
      });
    });
    req.on('error', reject);
    req.write(bodyBuf);
    req.end();
  });
}

async function getAccessToken(appid, secret) {
  const cachedToken = getReusableToken(config);
  if (cachedToken) {
    console.log('Using cached access token...');
    return cachedToken;
  }

  const res = await httpsGet(`https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=${appid}&secret=${secret}`);
  if (!res.access_token) {
    const cached = readTokenCache(config);
    if (cached?.accessToken && cached.expiresAt > Date.now()) {
      console.warn('getAccessToken failed, falling back to cached access token...');
      return cached.accessToken;
    }
    throw new Error('Failed to get access_token: ' + JSON.stringify(res));
  }
  writeTokenCache(config, res.access_token, { source: 'push_manual_review_to_feishu' });
  return res.access_token;
}

function buildMultipart(imgPath) {
  const imgData = fs.readFileSync(imgPath);
  const boundary = '----WxBoundary' + Date.now();
  const filename = path.basename(imgPath);
  const ext = path.extname(filename).toLowerCase().replace('.', '');
  const mime = ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : 'image/png';
  const header = Buffer.from(`--${boundary}
Content-Disposition: form-data; name="media"; filename="${filename}"
Content-Type: ${mime}

`);
  const footer = Buffer.from(`
--${boundary}--
`);
  return { body: Buffer.concat([header, imgData, footer]), contentType: `multipart/form-data; boundary=${boundary}` };
}

async function uploadContentImage(token, imgPath) {
  const { body, contentType } = buildMultipart(imgPath);
  const res = await httpsPost(`https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token=${token}`, body, contentType);
  if (!res.url) throw new Error('Content image upload failed: ' + JSON.stringify(res));
  return res.url;
}

async function main() {
  const opts = parseArgs();
  const articlePath = opts.article;
  const reviewPath = opts.review;
  const imagesDir = opts.images;
  const title = opts.title || '待人工审稿';
  const company = opts.company || '';
  const slot = opts.slot || 'manual';

  if (!articlePath || !reviewPath || !imagesDir) {
    throw new Error('缺少必要参数：--article --review --images');
  }

  const article = fs.readFileSync(articlePath, 'utf8');
  const review = fs.readFileSync(reviewPath, 'utf8');
  const appid = config.wechat.appId;
  const secret = config.wechat.appSecret;
  const token = await getAccessToken(appid, secret);

  const imageFiles = ['image_001.png', 'image_002.png', 'image_003.png', 'image_004.png']
    .map((name) => path.join(imagesDir, name))
    .filter((file) => fs.existsSync(file));

  const imageMarkdown = [];
  for (const file of imageFiles) {
    try {
      const url = await uploadContentImage(token, file);
      imageMarkdown.push(`![${path.basename(file)}](${url})`);
    } catch (err) {
      imageMarkdown.push(`- 图片上传失败：${path.basename(file)}｜${err.message}`);
    }
  }

  const docMarkdown = [
    `# 待人工审稿｜${title}`,
    '',
    `- 公司：${company || '未指定'}`,
    `- 批次：${slot}`,
    '- 状态：自动审稿连续 4 轮未通过，已停止继续改稿，转人工审稿。',
    `- article.md：${articlePath}`,
    `- review.md：${reviewPath}`,
    '',
    '---',
    '',
    '## 配图预览',
    '',
    ...imageMarkdown,
    '',
    '---',
    '',
    '## 当前稿件',
    '',
    article,
    '',
    '---',
    '',
    '## 审稿意见',
    '',
    review,
    '',
    '---',
    '',
    '## 人工处理建议',
    '',
    '- 先按 review.md 修正文稿。',
    '- 人工确认标题边界、风险判断、客户/订单表述。',
    '- 人工确认后，再决定是否推公众号草稿箱。',
    '',
  ].join('\n');

  const tempFile = path.join(os.tmpdir(), `manual_review_${Date.now()}.md`);
  fs.writeFileSync(tempFile, docMarkdown, 'utf8');
  const output = execFileSync('node', ['/home/ubuntu/.openclaw/workspace/scripts/feishu_doc_safe_write.js', '--mode', 'create', '--title', `待人工审稿｜${title}`, '--file', tempFile], { encoding: 'utf8' });
  const parsed = JSON.parse(output);
  console.log(JSON.stringify(parsed, null, 2));
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
