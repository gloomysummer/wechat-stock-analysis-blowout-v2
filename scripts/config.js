#!/usr/bin/env node
/**
 * 统一配置管理
 * 所有 API Key/Token 从环境变量读取，不再硬编码
 *
 * 环境变量优先级：
 * 1. 系统环境变量
 * 2. .env 文件（如果存在）
 * 3. 默认值（仅非敏感配置）
 */

const fs = require('fs');
const path = require('path');

function parseEnvContent(content, override = false) {
  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx === -1) continue;
    const key = trimmed.substring(0, eqIdx).trim();
    let value = trimmed.substring(eqIdx + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (override || !process.env[key]) {
      process.env[key] = value;
    }
  }
}

// 加载 .env 文件（简易实现，不依赖 dotenv）
function loadEnvFile() {
  const envPaths = [
    path.join(__dirname, '..', '.env'),
    path.join(process.cwd(), '.env'),
  ];

  for (const envPath of envPaths) {
    if (fs.existsSync(envPath)) {
      const content = fs.readFileSync(envPath, 'utf-8');
      parseEnvContent(content, false);
      break;
    }
  }
}

loadEnvFile();

function loadWechatCredentialProfile() {
  const profile = process.env.WECHAT_PUBLISH_PROFILE || '';
  const explicitFile = process.env.WECHAT_CREDENTIALS_FILE || '';

  let credentialFile = explicitFile;
  if (!credentialFile && profile === 'douyin_licai_piao') {
    credentialFile = '/home/ubuntu/.openclaw/credentials/wechat_douyin_licai_piao.env';
  }
  if (!credentialFile || !fs.existsSync(credentialFile)) return;

  const content = fs.readFileSync(credentialFile, 'utf-8');
  parseEnvContent(content, true);

  if (process.env.WECHAT_APPID) {
    process.env.WECHAT_APP_ID = process.env.WECHAT_APPID;
  }
  if (process.env.WECHAT_APPSECRET) {
    process.env.WECHAT_APP_SECRET = process.env.WECHAT_APPSECRET;
  }
}

loadWechatCredentialProfile();

// ─── 配置项 ─────────────────────────────────────────────────────────────────

const config = {
  // LLM 文章生成（主用百炼 qwen3.6-plus，备用 MiniMax）
  minimax: {
    apiKey: process.env.MINIMAX_API_KEY || process.env.DASHSCOPE_API_KEY || '',
    model: process.env.MINIMAX_MODEL || 'MiniMax-M2.5',
    baseUrl: process.env.MINIMAX_BASE_URL || 'https://api.minimaxi.com',
  },

  // Tushare 财报数据
  tushare: {
    token: process.env.TUSHARE_TOKEN || '',
  },

  // ModelScope AI 配图
  modelscope: {
    token: process.env.MODELSCOPE_TOKEN || '',
    model: process.env.MODELSCOPE_MODEL || 'Qwen/Qwen-Image-2512',
  },

  // 微信公众号
  wechat: {
    appId: process.env.WECHAT_APP_ID || '',
    appSecret: process.env.WECHAT_APP_SECRET || '',
  },

  // 发布配置
  publish: {
    serverIp: process.env.PUBLISH_SERVER_IP || '',
    serverPort: parseInt(process.env.PUBLISH_SERVER_PORT || '80', 10),
  },

  // 路径配置
  paths: {
    skillRoot: process.env.SKILL_ROOT || path.join(__dirname, '..'),
    outputDir: process.env.OUTPUT_DIR || path.join(__dirname, '..', 'output'),
    imagesDir: process.env.IMAGES_DIR || path.join(__dirname, '..', 'images'),
  },
};

/**
 * 校验必需的配置项
 * @param {string[]} requiredKeys - 需要的配置路径，如 ['qwencode.apiKey']
 * @throws {Error} 缺少配置时抛出错误
 */
function validateConfig(requiredKeys) {
  const missing = [];
  for (const keyPath of requiredKeys) {
    const parts = keyPath.split('.');
    let value = config;
    for (const part of parts) {
      value = value?.[part];
    }
    if (!value) {
      missing.push(keyPath);
    }
  }
  if (missing.length > 0) {
    const envVarHints = missing.map(k => {
      const envMap = {
        'qwencode.apiKey': 'DASHSCOPE_API_KEY',
        'tushare.token': 'TUSHARE_TOKEN',
        'modelscope.token': 'MODELSCOPE_TOKEN',
        'wechat.appId': 'WECHAT_APP_ID',
        'wechat.appSecret': 'WECHAT_APP_SECRET',
      };
      return `  ${k} → 环境变量: ${envMap[k] || k.toUpperCase().replace(/\./g, '_')}`;
    }).join('\n');
    throw new Error(`缺少必需配置:\n${envVarHints}\n\n请在 .env 文件中设置，或通过环境变量传入。`);
  }
}

module.exports = { config, validateConfig };
