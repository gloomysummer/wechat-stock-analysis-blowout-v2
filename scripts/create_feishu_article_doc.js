#!/usr/bin/env node
const fs = require('fs');
const os = require('os');
const path = require('path');
const https = require('https');
const { execFileSync } = require('child_process');

const { config } = require('./config');
const { getReusableToken, readTokenCache, writeTokenCache } = require('./wechat_token_cache');

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith('--')) continue;
    const key = arg.slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
    args[key] = value;
  }
  return args;
}

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          resolve(data);
        }
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
        try {
          resolve(JSON.parse(resp));
        } catch (e) {
          resolve(resp);
        }
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

  const res = await httpsGet(
    `https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=${appid}&secret=${secret}`,
  );
  if (!res.access_token) {
    const cached = readTokenCache(config);
    if (cached?.accessToken && cached.expiresAt > Date.now()) {
      console.warn('getAccessToken failed, falling back to cached access token...');
      return cached.accessToken;
    }
  }
  if (!res.access_token) {
    throw new Error(`Failed to get access_token: ${JSON.stringify(res)}`);
  }
  writeTokenCache(config, res.access_token, { source: 'create_feishu_article_doc' });
  return res.access_token;
}

function buildMultipart(imgPath) {
  const imgData = fs.readFileSync(imgPath);
  const boundary = '----WxBoundary' + Date.now();
  const filename = path.basename(imgPath);
  const ext = path.extname(filename).toLowerCase().replace('.', '');
  const mime = ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : 'image/png';
  const header = Buffer.from(
    `--${boundary}\r\nContent-Disposition: form-data; name="media"; filename="${filename}"\r\nContent-Type: ${mime}\r\n\r\n`,
  );
  const footer = Buffer.from(`\r\n--${boundary}--\r\n`);
  return { body: Buffer.concat([header, imgData, footer]), contentType: `multipart/form-data; boundary=${boundary}` };
}

async function uploadContentImage(token, imgPath) {
  const { body, contentType } = buildMultipart(imgPath);
  const res = await httpsPost(
    `https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token=${token}`,
    body,
    contentType,
  );
  if (!res.url) {
    throw new Error(`Content image upload failed: ${JSON.stringify(res)}`);
  }
  return res.url;
}

function inferTitle(articleMarkdown, fallbackTitle) {
  const firstHeading = articleMarkdown.match(/^#\s+(.+)$/m);
  return (firstHeading && firstHeading[1].trim()) || fallbackTitle || '带图文章';
}

async function main() {
  const args = parseArgs(process.argv);
  const articlePath = args.article ? path.resolve(args.article) : '';
  const imagesDir = args.images ? path.resolve(args.images) : '';
  const titleArg = args.title || '';

  if (!articlePath) {
    throw new Error('缺少参数：--article');
  }

  let article = fs.readFileSync(articlePath, 'utf8');
  const title = inferTitle(article, titleArg);

  const appId = config.wechat.appId;
  const appSecret = config.wechat.appSecret;
  if (!appId || !appSecret) {
    throw new Error('缺少 WECHAT_APP_ID / WECHAT_APP_SECRET，无法上传图片为可访问 URL');
  }

  const token = await getAccessToken(appId, appSecret);

  const imageMatches = Array.from(article.matchAll(/!\[([^\]]*)\]\(([^)]+)\)/g));
  const uploaded = new Map();

  for (const match of imageMatches) {
    const rawPath = match[2].trim();
    if (/^https?:\/\//i.test(rawPath)) continue;

    const absolutePath = path.isAbsolute(rawPath)
      ? rawPath
      : path.resolve(imagesDir || path.dirname(articlePath), rawPath);

    if (!fs.existsSync(absolutePath)) continue;
    if (uploaded.has(absolutePath)) continue;

    const url = await uploadContentImage(token, absolutePath);
    uploaded.set(absolutePath, url);
  }

  for (const match of imageMatches) {
    const alt = match[1];
    const rawPath = match[2].trim();
    if (/^https?:\/\//i.test(rawPath)) continue;

    const absolutePath = path.isAbsolute(rawPath)
      ? rawPath
      : path.resolve(imagesDir || path.dirname(articlePath), rawPath);

    const remoteUrl = uploaded.get(absolutePath);
    if (!remoteUrl) continue;
    article = article.replace(match[0], `![${alt}](${remoteUrl})`);
  }

  const tmpFile = path.join(os.tmpdir(), `feishu_article_${Date.now()}.md`);
  fs.writeFileSync(tmpFile, article, 'utf8');

  const output = execFileSync(
    'node',
    ['/home/ubuntu/.openclaw/workspace/scripts/feishu_doc_safe_write.js', '--mode', 'create', '--title', title, '--file', tmpFile],
    { encoding: 'utf8' },
  );
  process.stdout.write(output);
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
