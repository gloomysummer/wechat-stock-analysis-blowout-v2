#!/usr/bin/env node
/**
 * 在线发布脚本
 * 将生成的 HTML 文章发布到固定地址
 */

const fs = require('fs');
const path = require('path');
const { config } = require('./config');

const OUTPUT_DIR = path.join(config.paths.skillRoot, 'output');
const INDEX_FILE = path.join(OUTPUT_DIR, 'index.html');

/**
 * 发布 HTML 文章
 */
function publishArticle(articlePath) {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  // 读取文章 HTML
  let html = fs.readFileSync(articlePath, 'utf-8');

  // 提取图片文件名，复制到输出目录
  const imgMatches = html.match(/src="([^"]+\.png)"/g) || [];
  const imgFiles = [
    ...new Set(imgMatches.map((m) => m.match(/src="([^"]+)"/)[1])),
  ];

  for (const imgFile of imgFiles) {
    const srcPath = path.join(path.dirname(articlePath), imgFile);
    const destPath = path.join(OUTPUT_DIR, path.basename(imgFile));
    if (fs.existsSync(srcPath)) {
      fs.copyFileSync(srcPath, destPath);
      console.log(`  📷 复制图片: ${path.basename(imgFile)}`);
    }
  }

  // 修复 HTML 中的图片路径
  html = html.replace(/src="\.\.\/[^"]+"/g, (match) => {
    return 'src="' + match.replace('../', '') + '"';
  });

  // 写入 index.html
  fs.writeFileSync(INDEX_FILE, html);
  fs.writeFileSync(path.join(OUTPUT_DIR, 'article.html'), html);

  const serverUrl = `http://${config.publish.serverIp}:${config.publish.serverPort}`;
  console.log(`\n✅ 已发布: ${serverUrl}\n`);
}

module.exports = { publishArticle, OUTPUT_DIR, INDEX_FILE };

// CLI 入口
if (require.main === module) {
  const articlePath = process.argv[2];
  if (!articlePath) {
    console.log('用法: node publish_online.js <article.html>');
    process.exit(1);
  }
  publishArticle(articlePath);
}
