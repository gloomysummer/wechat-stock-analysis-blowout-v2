#!/usr/bin/env node
/**
 * 公众号草稿箱一键发布
 * 将已生成的文章（HTML + 图片）推送到微信公众号草稿箱
 *
 * 用法: node publish_draft.js <公司名称> [输出目录]
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { config, validateConfig } = require("./config");

const PUBLISH_SCRIPT = path.join(__dirname, "publish_to_wechat.js");
const GENERATE_HTML_SCRIPT = path.join(__dirname, "generate_html.js");

function findOutputDir(companyName, outputDirOverride) {
  if (outputDirOverride && fs.existsSync(outputDirOverride)) return outputDirOverride;
  const companyDir = path.join(config.paths.outputDir, companyName);
  if (fs.existsSync(companyDir)) return companyDir;
  if (fs.existsSync(config.paths.outputDir)) return config.paths.outputDir;
  throw new Error("找不到输出目录");
}

function findArticleMd(outputDir) {
  const articlePath = path.join(outputDir, "article.md");
  if (fs.existsSync(articlePath)) return articlePath;
  throw new Error("找不到 article.md，请确认目录 " + outputDir + " 下有文章");
}

function findOrGenerateHtml(outputDir, articlePath, imagesDir) {
  const htmlPath = path.join(outputDir, "article.html");
  console.log(fs.existsSync(htmlPath)
    ? "📄 检测到已有 article.html，按当前模板重新生成，避免沿用旧样式..."
    : "📄 未找到 article.html，正在生成...");
  execFileSync("node", [GENERATE_HTML_SCRIPT, articlePath, imagesDir, htmlPath], {
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  });
  if (fs.existsSync(htmlPath)) return htmlPath;
  const indexPath = path.join(outputDir, "index.html");
  if (fs.existsSync(indexPath)) return indexPath;
  throw new Error("无法生成 HTML");
}

function findCoverImage(imagesDir, outputDir = "") {
  const candidates = [];
  if (fs.existsSync(imagesDir)) {
    const files = fs.readdirSync(imagesDir);
    const cover = files.find(f => f.includes("image_001") && /\.(png|jpg|jpeg|webp)$/i.test(f));
    if (cover) return path.join(imagesDir, cover);
    for (const f of files.filter(f => /\.(png|jpg|jpeg|webp)$/i.test(f)).sort()) {
      candidates.push(path.join(imagesDir, f));
    }
  }
  if (outputDir && fs.existsSync(outputDir)) {
    const rootFiles = fs.readdirSync(outputDir)
      .filter(f => /^(dyg_.*|image_001).*\.(png|jpg|jpeg|webp)$/i.test(f))
      .sort();
    for (const f of rootFiles) {
      candidates.push(path.join(outputDir, f));
    }
  }
  return candidates[0] || "";
}

function findContentImages(imagesDir) {
  if (!fs.existsSync(imagesDir)) return [];
  return fs.readdirSync(imagesDir)
    .filter(f => /^image_\d{3}\.\w+$/i.test(f))
    .sort()
    .map(f => path.join(imagesDir, f));
}

function extractTitle(articlePath) {
  const content = fs.readFileSync(articlePath, "utf8");
  const match = content.match(/^#\s+(.+)$/m);
  if (match) return match[1].trim();
  return path.basename(path.dirname(articlePath));
}

function main() {
  const args = process.argv.slice(2);
  let companyName = "";
  let outputDir = "";

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--company" && args[i + 1]) {
      companyName = args[++i];
    } else if (args[i] === "--output-dir" && args[i + 1]) {
      outputDir = args[++i];
    } else if (!companyName) {
      companyName = args[i];
    } else if (!outputDir) {
      outputDir = args[i];
    }
  }

  if (!companyName) {
    console.error("用法: node publish_draft.js <公司名称> [--output-dir /path]");
    process.exit(1);
  }

  console.log("📤 准备推送 \"" + companyName + "\" 到公众号草稿箱...");
  validateConfig(["wechat.appId", "wechat.appSecret"]);

  try {
    const targetDir = findOutputDir(companyName, outputDir);
    const articlePath = findArticleMd(targetDir);
    const imagesDir = path.join(targetDir, "images");
    const htmlPath = findOrGenerateHtml(targetDir, articlePath, imagesDir);
    const title = extractTitle(articlePath);
    const coverPath = findCoverImage(imagesDir, targetDir);
    const contentImages = findContentImages(imagesDir);

    if (!coverPath) {
      console.error("❌ 找不到封面图片");
      process.exit(1);
    }

    console.log("   文章: " + articlePath);
    console.log("   HTML: " + htmlPath);
    console.log("   封面: " + coverPath);
    console.log("   配图: " + contentImages.length + " 张");
    console.log("");

    const cmd = [PUBLISH_SCRIPT, "--html", htmlPath, "--cover", coverPath, "--title", title];
    contentImages.forEach((imgPath, idx) => {
      cmd.push("--image" + (idx + 1), imgPath);
    });

    console.log("🚀 正在推送...");
    execFileSync("node", cmd, {
      encoding: "utf8",
      maxBuffer: 30 * 1024 * 1024,
      stdio: "inherit",
    });

    console.log("\n✅ 成功！草稿已推送到微信公众号草稿箱");
    console.log("📱 下一步: 登录 mp.weixin.qq.com → 内容管理 → 草稿箱 → 检查并发布");

  } catch (err) {
    console.error("\n❌ 推送失败: " + err.message);
    process.exit(1);
  }
}

main();
