#!/usr/bin/env node
/**
 * WeChat Official Account Draft Publisher v2
 * 微信公众号草稿箱推送脚本
 *
 * 核心改进：不再自行转换 Markdown，直接使用 generate_html.js 生成的 HTML，
 *           只替换本地图片路径为微信上传后的 URL。
 *
 * Usage:
 *   node publish_to_wechat.js \
 *     --appid <APP_ID> \
 *     --secret <APP_SECRET> \
 *     --html <path/to/article.html> \
 *     --cover <path/to/cover.png> \
 *     --image1 <path/to/image1.png> \
 *     --image2 <path/to/image2.png> \
 *     --image3 <path/to/image3.png> \
 *     --title "文章标题" \
 *     --digest "摘要（可选）" \
 *     --author "作者名（可选）"
 *
 * 也可通过环境变量 WECHAT_APP_ID 和 WECHAT_APP_SECRET 传入凭据。
 */

const fs = require('fs');
const https = require('https');
const path = require('path');
const { config } = require('./config');
const { getReusableToken, readTokenCache, writeTokenCache } = require('./wechat_token_cache');

// ─── CLI Arg Parser ───────────────────────────────────────────────────────────
function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('--')) {
      opts[args[i].slice(2)] = args[i + 1];
      i++;
    }
  }
  return opts;
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────
function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { resolve(data); }
      });
      res.on('error', reject);
    });
  });
}

function httpsPost(url, bodyBuf, contentType) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const options = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + urlObj.search,
      method: 'POST',
      headers: {
        'Content-Type': contentType,
        'Content-Length': bodyBuf.length,
      },
    };
    const req = https.request(options, (res) => {
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

function postJson(url, obj) {
  return httpsPost(url, Buffer.from(JSON.stringify(obj)), 'application/json');
}

function formatWeChatApiError(stage, payload) {
  if (!payload || typeof payload !== 'object') {
    return `${stage} failed: ${String(payload)}`;
  }
  const errcode = payload.errcode ?? 'unknown';
  const errmsg = payload.errmsg ?? 'unknown';
  return `${stage} failed: errcode=${errcode}, errmsg=${errmsg}, raw=${JSON.stringify(payload)}`;
}

// ─── WeChat API ───────────────────────────────────────────────────────────────
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
    throw new Error(formatWeChatApiError('getAccessToken', res));
  }
  writeTokenCache(config, res.access_token, { source: 'publish_to_wechat' });
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
  return {
    body: Buffer.concat([header, imgData, footer]),
    contentType: `multipart/form-data; boundary=${boundary}`,
  };
}

/** Upload cover image → returns media_id (permanent material) */
async function uploadCover(token, imgPath) {
  const { body, contentType } = buildMultipart(imgPath);
  const res = await httpsPost(
    `https://api.weixin.qq.com/cgi-bin/material/add_material?access_token=${token}&type=image`,
    body,
    contentType,
  );
  if (!res.media_id)
    throw new Error(formatWeChatApiError('uploadCover', res));
  console.log('[OK] Cover uploaded, media_id:', res.media_id);
  return res.media_id;
}

/** Upload content image → returns URL for embedding in HTML */
async function uploadContentImage(token, imgPath) {
  const { body, contentType } = buildMultipart(imgPath);
  const res = await httpsPost(
    `https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token=${token}`,
    body,
    contentType,
  );
  if (!res.url)
    throw new Error(formatWeChatApiError(`uploadContentImage(${path.basename(imgPath)})`, res));
  console.log('[OK] Image uploaded:', path.basename(imgPath), '→', res.url);
  return res.url;
}

// ─── HTML Processing ──────────────────────────────────────────────────────────

/**
 * 从已生成的 HTML 文件中提取 <body> 内容，
 * 并替换本地图片路径为微信上传后的 URL
 */
function processHtml(htmlContent, imageMap) {
  let content = htmlContent;

  // 提取 <body>...</body> 内容
  const bodyMatch = content.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
  if (bodyMatch) {
    content = bodyMatch[1].trim();
  }

  // 【修复1】移除 H1 标题（微信已有标题栏，避免重复）
  content = content.replace(/<h1[^>]*>[\s\S]*?<\/h1>/gi, '');

  // 去掉预览页专用的外层容器样式，避免微信正文左右留白过大
  content = content.replace(/max-width:\s*800px;/g, 'max-width:100%;');
  content = content.replace(/margin:\s*0\s*auto;/g, 'margin:0;');
  content = content.replace(/background:\s*#fff;/g, 'background:transparent;');
  content = content.replace(/padding:\s*30px;/g, 'padding:0;');
  content = content.replace(/box-shadow:[^;]+;/g, '');

  // 图片在微信正文里尽量铺满正文宽度，不保留预览页的缩窄效果
  content = content.replace(/max-width:\s*90%;/g, 'max-width:100%;width:100%;');
  content = content.replace(/border-radius:\s*8px;/g, 'border-radius:0;');
  content = content.replace(/text-align:\s*center;margin:\s*25px 0;/g, 'text-align:center;margin:18px 0;');

  // 替换所有本地图片路径为微信 URL
  for (const [localName, wxUrl] of Object.entries(imageMap)) {
    const re = new RegExp(`src="([^"]*?)${escapeRegex(localName)}"`, 'g');
    content = content.replace(re, `src="${wxUrl}"`);
  }

  // 【修复2】删除未被替换的本地图片（没有上传到微信的图片，显示不出来）
  content = content.replace(/<div class="section-img">\s*<img src="[^"]*?\.png"[^>]*>\s*<\/div>/g, '');
  content = content.replace(/<img src="(?!https?:\/\/)[^"]*"[^>]*>/g, '');

  // 确保所有 img 标签都有 data-src（微信要求）
  content = content.replace(
    /<img\s+([^>]*?)src="(https?:\/\/[^"]+)"([^>]*?)>/g,
    '<img $1src="$2" data-src="$2"$3>'
  );

  return `<section style="font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;padding:0;">${content}</section>`;
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function sanitizeTitle(title) {
  return String(title || '')
    .replace(/[*_`#~]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function normalizeAuthor(author) {
  const cleaned = String(author || '').trim();
  return cleaned || '光影资本Pro';
}

// ─── Draft creation ───────────────────────────────────────────────────────────
async function createDraft(token, { title, author, digest, htmlContent, thumbMediaId }) {
  const res = await postJson(
    `https://api.weixin.qq.com/cgi-bin/draft/add?access_token=${token}`,
    {
      articles: [
        {
          title,
          author: author || '',
          digest: digest || '',
          content: htmlContent,
          content_source_url: '',
          thumb_media_id: thumbMediaId,
          need_open_comment: 1,
          only_fans_can_comment: 0,
        },
      ],
    },
  );
  if (!res.media_id)
    throw new Error(formatWeChatApiError('createDraft', res));
  console.log('[OK] Draft created, media_id:', res.media_id);
  return res.media_id;
}

// ─── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  const opts = parseArgs();

  const appid = opts.appid || config.wechat.appId;
  const secret = opts.secret || config.wechat.appSecret;

  if (!appid || !secret) {
    console.error('缺少微信凭据。通过 --appid/--secret 或 WECHAT_APP_ID/WECHAT_APP_SECRET 环境变量传入。');
    process.exit(1);
  }

  // 支持 --html（推荐）或 --article（兼容旧版）
  const htmlPath = opts.html || '';
  const articlePath = opts.article || '';

  if (!htmlPath && !articlePath) {
    console.error('请提供 --html <path/to/article.html> 或 --article <path/to/article.md>');
    process.exit(1);
  }

  const required = ['cover', 'title'];
  for (const key of required) {
    if (!opts[key]) {
      console.error(`Missing required argument: --${key}`);
      process.exit(1);
    }
  }

  // 收集需要上传的配图
  const imageFiles = [];
  for (let i = 1; i <= 10; i++) {
    const key = `image${i}`;
    if (opts[key] && fs.existsSync(opts[key])) {
      imageFiles.push({ key, path: opts[key], name: path.basename(opts[key]) });
    }
  }

  // 摘要
  let digest = opts.digest || '';
  if (!digest) {
    if (htmlPath && fs.existsSync(htmlPath)) {
      const raw = fs.readFileSync(htmlPath, 'utf8').replace(/<[^>]+>/g, '');
      digest = raw.replace(/\s+/g, ' ').trim().slice(0, 100) + '…';
    } else if (articlePath && fs.existsSync(articlePath)) {
      const md = fs.readFileSync(articlePath, 'utf8');
      digest = md.replace(/[#*\[\]`>|{}\-!]/g, '').replace(/\n+/g, ' ').trim().slice(0, 100) + '…';
    }
  }

  console.log('Getting access token...');
  const token = await getAccessToken(appid, secret);

  console.log('Uploading cover...');
  const thumbMediaId = await uploadCover(token, opts.cover);

  // 上传配图
  const imageMap = {};
  if (imageFiles.length > 0) {
    console.log(`Uploading ${imageFiles.length} content images...`);
    for (const img of imageFiles) {
      const url = await uploadContentImage(token, img.path);
      imageMap[img.name] = url;
    }
  }

  // 生成 HTML 内容
  let htmlContent = '';
  if (htmlPath && fs.existsSync(htmlPath)) {
    console.log(`Reading pre-built HTML: ${htmlPath}`);
    const rawHtml = fs.readFileSync(htmlPath, 'utf8');
    htmlContent = processHtml(rawHtml, imageMap);
  } else if (articlePath && fs.existsSync(articlePath)) {
    // 兼容旧版：尝试找同目录下的 article.html
    const dir = path.dirname(articlePath);
    const possibleHtml = path.join(dir, 'article.html');
    if (fs.existsSync(possibleHtml)) {
      console.log(`Found pre-built HTML: ${possibleHtml}`);
      const rawHtml = fs.readFileSync(possibleHtml, 'utf8');
      htmlContent = processHtml(rawHtml, imageMap);
    } else {
      console.error('❌ 未找到 article.html。请先运行 generate_html.js 生成 HTML。');
      process.exit(1);
    }
  }

  const cleanTitle = sanitizeTitle(opts.title);
  const author = normalizeAuthor(opts.author);

  console.log('Creating draft...');
  const draftMediaId = await createDraft(token, {
    title: cleanTitle,
    author,
    digest,
    htmlContent,
    thumbMediaId,
  });

  console.log('\n✅ Done! Draft pushed to 草稿箱.');
  console.log('Draft media_id:', draftMediaId);
  console.log('Next: login to mp.weixin.qq.com → 内容管理 → 草稿箱 → 发布');
}

main().catch((err) => {
  console.error('❌ Error:', err.message || err);
  process.exit(1);
});
