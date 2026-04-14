#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const CACHE_TTL_MS = 7000 * 1000;
const CACHE_SKEW_MS = 2 * 60 * 1000;

function getCacheFilePath(config) {
  const skillRoot = config?.paths?.skillRoot || path.join(__dirname, '..');
  return path.join(skillRoot, '.cache', 'wechat_access_token.json');
}

function readTokenCache(config) {
  const filePath = getCacheFilePath(config);
  try {
    if (!fs.existsSync(filePath)) return null;
    const raw = fs.readFileSync(filePath, 'utf8');
    const parsed = JSON.parse(raw);
    if (!parsed?.accessToken || !parsed?.expiresAt) return null;
    return parsed;
  } catch (err) {
    return null;
  }
}

function writeTokenCache(config, accessToken, meta = {}) {
  const filePath = getCacheFilePath(config);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const payload = {
    accessToken,
    expiresAt: Date.now() + CACHE_TTL_MS,
    cachedAt: new Date().toISOString(),
    ...meta,
  };
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2), 'utf8');
  return payload;
}

function getReusableToken(config) {
  const cached = readTokenCache(config);
  if (!cached) return null;
  if (cached.expiresAt <= Date.now() + CACHE_SKEW_MS) return null;
  return cached.accessToken;
}

module.exports = {
  getCacheFilePath,
  getReusableToken,
  readTokenCache,
  writeTokenCache,
};
