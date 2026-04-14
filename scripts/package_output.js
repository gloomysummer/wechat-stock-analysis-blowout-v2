#!/usr/bin/env node
/**
 * 打包文章产物，供微信当前会话发送 zip 使用。
 *
 * 用法:
 *   node package_output.js <公司名称> [--output-dir /abs/path] [--zip-path /abs/path.zip]
 */

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
let config = { paths: { outputDir: '' } };
try {
  ({ config } = require('./config'));
} catch (err) {
  // Allow explicit --output-dir usage even when config.js is unavailable.
}

function parseArgs(argv) {
  const args = argv.slice(2);
  const result = { companyName: '', outputDir: '', zipPath: '' };
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--output-dir' && args[i + 1]) {
      result.outputDir = args[++i];
    } else if (arg === '--zip-path' && args[i + 1]) {
      result.zipPath = args[++i];
    } else if (!result.companyName) {
      result.companyName = arg;
    }
  }
  return result;
}

function resolveOutputDir(companyName, outputDirOverride) {
  if (outputDirOverride) {
    const abs = path.resolve(outputDirOverride);
    if (fs.existsSync(abs)) return abs;
    throw new Error(`指定输出目录不存在: ${abs}`);
  }

  if (!companyName) {
    throw new Error('缺少公司名称，且未指定 --output-dir');
  }

  const byName = path.join(config.paths.outputDir, companyName);
  if (fs.existsSync(byName)) return byName;

  const slugDir = path.join(config.paths.outputDir, slugify(companyName));
  if (fs.existsSync(slugDir)) return slugDir;

  throw new Error(`找不到输出目录: ${byName}`);
}

function slugify(input) {
  return String(input || '')
    .trim()
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, '_');
}

function defaultZipPath(outputDir, companyName) {
  const baseName = slugify(companyName || path.basename(outputDir) || 'article');
  return path.join(outputDir, `${baseName}_wechat_package.zip`);
}

function collectEntries(outputDir) {
  const preferred = [
    'article.md',
    'article.html',
    'index.html',
    'review.md',
    'financial_analysis.md',
    'company_profile_brief.md',
    'risk_brief.md',
    'external_risk_brief.md',
    'generation_status.json',
    'risk_seed_data.json',
    'financial_data.json',
    'images',
  ];

  const entries = [];
  for (const name of preferred) {
    const target = path.join(outputDir, name);
    if (fs.existsSync(target)) {
      entries.push(name);
    }
  }

  // 额外收录 output 目录下所有 dyg_*.png 配图（兜底）
  try {
    const allFiles = fs.readdirSync(outputDir);
    for (const f of allFiles) {
      if (/^dyg_.*.png$/i.test(f) && !entries.includes(f)) {
        entries.push(f);
      }
    }
  } catch (_) {}

  if (entries.length === 0) {
    throw new Error(`输出目录为空，无法打包: ${outputDir}`);
  }
  return entries;
}

function ensureZipTool() {
  try {
    execFileSync('zip', ['-v'], { stdio: 'ignore' });
  } catch (err) {
    throw new Error('系统未安装 zip 命令，无法打包 zip');
  }
}

function createZip(outputDir, zipPath, entries) {
  if (fs.existsSync(zipPath)) {
    fs.unlinkSync(zipPath);
  }
  ensureZipTool();
  execFileSync('zip', ['-r', zipPath, ...entries], {
    cwd: outputDir,
    stdio: 'inherit',
    maxBuffer: 20 * 1024 * 1024,
  });

  if (!fs.existsSync(zipPath)) {
    throw new Error(`zip 未生成成功: ${zipPath}`);
  }
}

function main() {
  const { companyName, outputDir, zipPath } = parseArgs(process.argv);
  const resolvedOutputDir = resolveOutputDir(companyName, outputDir);
  const resolvedZipPath = path.resolve(zipPath || defaultZipPath(resolvedOutputDir, companyName));
  const entries = collectEntries(resolvedOutputDir);

  console.log(`📦 打包文章产物: ${resolvedOutputDir}`);
  console.log(`   包含内容: ${entries.join(', ')}`);
  createZip(resolvedOutputDir, resolvedZipPath, entries);

  const stat = fs.statSync(resolvedZipPath);
  console.log(`✅ ZIP 已生成: ${resolvedZipPath}`);
  console.log(`   大小: ${stat.size} bytes`);
}

main();
