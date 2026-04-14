const { generateImagesParallel } = require('./scripts/generate_images');
const { config } = require('./scripts/config');
const path = require('path');

const imagesDir = path.join(__dirname, 'output/popmart/images');

// LABUBU 主题提示词
const prompts = [
  {
    prompt: "Cute monster doll LABUBU from POP MART, vinyl toy figure, big eyes, sharp teeth, furry texture, green and brown colors, collectible art toy, studio lighting, product photography style, white background",
    filename: 'image_001.png'
  },
  {
    prompt: "POP MART blind box store display, colorful toy shelves, mystery boxes, LABUBU and other designer toys, modern retail environment, bright lighting, commercial photography",
    filename: 'image_002.png'
  },
  {
    prompt: "Financial chart showing POP MART stock performance, revenue growth bar chart, stock price decline graph, red and green colors, professional financial data visualization, clean design",
    filename: 'image_003.png'
  },
  {
    prompt: "Cute LABUBU character waving goodbye, POP MART style, friendly monster doll, subscribe call-to-action, minimalist illustration, blue and white theme",
    filename: 'image_004.png'
  }
];

console.log('🎨 重新生成 LABUBU 主题配图...\n');
generateImagesParallel(config.modelscope.token, prompts, imagesDir)
  .then(results => {
    const successCount = results.filter(r => r.success).length;
    console.log(`\n✅ 完成！成功 ${successCount}/${prompts.length}`);
    process.exit(successCount === prompts.length ? 0 : 1);
  })
  .catch(err => {
    console.error('❌ 失败:', err.message);
    process.exit(1);
  });
