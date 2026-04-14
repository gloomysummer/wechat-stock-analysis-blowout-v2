#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const articlePath = args[0] || './output/article.md';
const imagesDir = args[1] || './output';
const outputPath = args[2] || './output/article.html';

console.log(`article: ${articlePath}`);
console.log(`images: ${imagesDir}`);

const articleContent = fs.readFileSync(articlePath, 'utf8');

const S = {
  body: "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;margin:0;padding:20px;background:#fafafa;",
  container: 'max-width:800px;margin:0 auto;background:#fff;padding:30px;box-shadow:0 2px 10px rgba(0,0,0,0.05);',
  h1: 'font-size:22px;text-align:center;margin-bottom:30px;color:#1a1a1a;line-height:1.5;font-weight:bold;',
  h2: 'font-size:18px;margin-top:35px;margin-bottom:15px;border-left:4px solid #1a73e8;padding-left:12px;color:#1a1a1a;font-weight:bold;',
  h3: 'font-size:15px;margin-top:20px;margin-bottom:10px;color:#333;font-weight:bold;',
  p: 'font-size:15px;line-height:1.85;color:#333;margin:10px 0;',
  strong: 'color:#1a73e8;font-weight:bold;',
  imgWrap: 'text-align:center;margin:25px 0;',
  img: 'max-width:90%;border-radius:8px;',
  th: 'padding:10px 12px;text-align:left;background:#1a73e8;color:white;font-weight:600;border:1px solid #1a73e8;',
  td: 'padding:10px 12px;text-align:left;border:1px solid #e0e0e0;',
  tdEven: 'padding:10px 12px;text-align:left;border:1px solid #e0e0e0;background:#f8f9fa;',
  table: 'border-collapse:collapse;width:100%;margin:15px 0;font-size:14px;',
  pre: 'background:#f5f5f5;border-radius:6px;padding:14px 18px;font-size:14px;line-height:1.6;overflow-x:auto;margin:12px 0;font-family:Menlo,Monaco,monospace;white-space:pre-wrap;word-break:break-all;',
  blockquote: 'background:#f5f5f5;border-left:4px solid #ff9800;padding:12px 16px;margin:12px 0;border-radius:4px;font-size:14px;line-height:1.7;',
  footer: 'text-align:center;margin-top:40px;padding:25px;color:#666;font-size:15px;border-top:1px solid #eee;',
};

function discoverImages(dir) {
  if (!fs.existsSync(dir)) return { cover: null, business: null, financial: null, ending: null };
  const allImages = fs.readdirSync(dir).filter((f) => !f.startsWith('.') && !f.startsWith('._') && /\.(png|jpg|jpeg)$/i.test(f));
  if (allImages.length === 0) return { cover: null, business: null, financial: null, ending: null };

  const result = { cover: null, business: null, financial: null, ending: null };
  const used = new Set();
  const rules = [
    { role: 'cover', keywords: ['cover', 'company', 'image_001'] },
    { role: 'business', keywords: ['product', 'business', 'factory', 'image_002'] },
    { role: 'financial', keywords: ['financial', 'finance', 'data', 'chart', 'image_003'] },
    { role: 'ending', keywords: ['ending', 'end', 'thank', 'image_004'] },
  ];

  for (const rule of rules) {
    for (const file of allImages) {
      if (used.has(file)) continue;
      const lower = file.toLowerCase();
      if (rule.keywords.some((kw) => lower.includes(kw))) {
        result[rule.role] = file;
        used.add(file);
        break;
      }
    }
  }

  const remaining = allImages.filter((f) => !used.has(f));
  let idx = 0;
  for (const role of ['cover', 'business', 'financial', 'ending']) {
    if (!result[role] && idx < remaining.length) result[role] = remaining[idx++];
  }
  return result;
}

function imgTag(filename, alt = '') {
  if (!filename) return '';
  return `<div style="${S.imgWrap}"><img src="${filename}" alt="${alt}" style="${S.img}"/></div>\n`;
}

function processInline(text) {
  text = text.replace(/\*\*\*(.+?)\*\*\*/g, `<strong style="${S.strong}"><em>$1</em></strong>`);
  text = text.replace(/\*\*(.+?)\*\*/g, `<strong style="${S.strong}">$1</strong>`);
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  return text;
}

function isAuthorLine(text) {
  return text.startsWith('作者:') || text.startsWith('作者：') || text.startsWith('**作者:') || text.startsWith('**作者：');
}

function isDisclaimerLine(text) {
  return /免责声明|不构成任何投资建议|市场有风险，投资需谨慎/.test(text);
}

const imageMap = discoverImages(imagesDir);
const foundImages = Object.entries(imageMap).filter(([_, v]) => v).map(([k, v]) => `${k}=${v}`);
console.log(`image mapping: ${foundImages.length > 0 ? foundImages.join(', ') : 'none'}`);

const placeholderMap = {
  '[插入配图：公司/工厂]': imageMap.cover,
  '[插入配图：核心产品/业务]': imageMap.business,
  '[插入配图：财务数据]': imageMap.financial,
  '[插入配图：结尾]': imageMap.ending,
};

let html = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>上市公司分析</title>
</head>
<body style="${S.body}">
<div style="${S.container}">
`;

const lines = articleContent.split('\n');
let inTable = false;
let inCode = false;
let codeContent = '';
let tableRowIndex = 0;
let endingInserted = false;

for (const rawLine of lines) {
  const trimmed = rawLine.trim();

  if (!trimmed) {
    if (inTable) {
      html += '</table>\n';
      inTable = false;
      tableRowIndex = 0;
    }
    continue;
  }

  if (trimmed.startsWith('```')) {
    if (!inCode) {
      inCode = true;
      codeContent = '';
    } else {
      html += `<pre style="${S.pre}">${codeContent.trim()}</pre>\n`;
      inCode = false;
    }
    continue;
  }

  if (inCode) {
    codeContent += rawLine + '\n';
    continue;
  }

  if (/^-{3,}$/.test(trimmed) || /^\*{3,}$/.test(trimmed)) continue;
  if (isAuthorLine(trimmed) || isDisclaimerLine(trimmed)) continue;

  const mdImage = trimmed.match(/^!\[(.*?)\]\((.+?)\)$/);
  if (mdImage) {
    if (inTable) {
      html += '</table>\n';
      inTable = false;
      tableRowIndex = 0;
    }
    html += imgTag(mdImage[2], mdImage[1]);
    if ((mdImage[2] || '').includes('image_004')) endingInserted = true;
    continue;
  }

  if (Object.prototype.hasOwnProperty.call(placeholderMap, trimmed)) {
    if (inTable) {
      html += '</table>\n';
      inTable = false;
      tableRowIndex = 0;
    }
    html += imgTag(placeholderMap[trimmed]);
    if (trimmed === '[插入配图：结尾]') endingInserted = true;
    continue;
  }

  if (trimmed.startsWith('> ')) {
    html += `<blockquote style="${S.blockquote}">${processInline(trimmed.replace(/^>\s+/, ''))}</blockquote>\n`;
    continue;
  }

  if (trimmed.startsWith('# ') && !trimmed.startsWith('## ')) {
    html += `<h1 style="${S.h1}">${trimmed.replace(/^# /, '')}</h1>\n`;
    continue;
  }

  if (trimmed.startsWith('## ')) {
    if (inTable) {
      html += '</table>\n';
      inTable = false;
      tableRowIndex = 0;
    }
    html += `<h2 style="${S.h2}">${trimmed.replace(/^## /, '')}</h2>\n`;
    continue;
  }

  if (trimmed.startsWith('### ')) {
    html += `<h3 style="${S.h3}">${trimmed.replace(/^### /, '')}</h3>\n`;
    continue;
  }

  if (trimmed.startsWith('|')) {
    if (/^\|[\s\-:|]+\|$/.test(trimmed)) continue;
    const rawCells = trimmed.split('|');
    const cells = rawCells.slice(1, rawCells.length - 1);
    if (cells.length > 1) {
      if (!inTable) {
        html += `<table style="${S.table}">\n<tr>`;
        for (const cell of cells) {
          html += `<th style="${S.th}">${processInline(cell.trim()) || '&nbsp;'}</th>`;
        }
        html += '</tr>\n';
        inTable = true;
        tableRowIndex = 0;
        continue;
      }
      tableRowIndex += 1;
      const tdStyle = tableRowIndex % 2 === 0 ? S.tdEven : S.td;
      html += '<tr>';
      for (const cell of cells) {
        html += `<td style="${tdStyle}">${processInline(cell.trim()) || '&nbsp;'}</td>`;
      }
      html += '</tr>\n';
    }
    continue;
  }

  const orderedWithBold = trimmed.match(/^\d+\.\s+\*\*(.+?)\*\*[：:](.+)$/);
  if (orderedWithBold) {
    html += `<p style="${S.p}">- <strong style="${S.strong}">${orderedWithBold[1]}</strong>：${processInline(orderedWithBold[2].trim())}</p>\n`;
    continue;
  }

  if (/^\d+\.\s+/.test(trimmed)) {
    html += `<p style="${S.p}">- ${processInline(trimmed.replace(/^\d+\.\s+/, ''))}</p>\n`;
    continue;
  }

  if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
    html += `<p style="${S.p}">- ${processInline(trimmed.replace(/^[-*]\s+/, ''))}</p>\n`;
    continue;
  }

  if (inTable) {
    html += '</table>\n';
    inTable = false;
    tableRowIndex = 0;
  }

  html += `<p style="${S.p}">${processInline(trimmed)}</p>\n`;
}

if (inTable) html += '</table>\n';
html += `<div style="${S.footer}">\n`;
if (!endingInserted && imageMap.ending) html += imgTag(imageMap.ending, '结尾配图');
html += `<p style="font-size:15px;color:#666;">本文仅用于信息分享与案例拆解，不构成任何投资建议。</p>\n`;
html += `<p style="font-size:12px;color:#999;margin-top:20px;border-top:1px solid #eee;padding-top:15px;">市场有风险，投资需谨慎。涉及的财务数据与公开信息请以公司公告和权威披露为准。</p>\n`;
html += `</div>\n`;
html += '</div>\n</body>\n</html>';

// 确保输出目录存在
const outputDir = path.dirname(outputPath);
if (!fs.existsSync(outputDir)) {
  fs.mkdirSync(outputDir, { recursive: true });
}

fs.writeFileSync(outputPath, html);
const indexPath = path.join(path.dirname(outputPath), 'index.html');
fs.writeFileSync(indexPath, html);

console.log(`generated html: ${outputPath}`);
console.log(`generated index: ${indexPath}`);
