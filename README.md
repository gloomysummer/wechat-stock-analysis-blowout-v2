# wechat-stock-analysis-blowout-v2

OpenClaw skill for generating deep-dive WeChat articles about listed companies with a full delivery pipeline:

- financial analysis
- company/risk briefs
- draft writing + review loop
- image generation
- WeChat draft publishing
- ZIP packaging

It supports both A-shares and Hong Kong stocks. For Hong Kong stocks such as `09992.HK`, the skill uses `akshare` to fetch financial data.

## What is included

- `SKILL.md`: the runtime instructions OpenClaw reads
- `scripts/financial_analysis.py`: structured financial analysis, including HK stock support
- `scripts/write_article.js`: main pipeline entry
- `scripts/publish_draft.js`: WeChat draft publishing wrapper
- `scripts/publish_to_wechat.js`: WeChat API publishing logic
- risk tracing docs and content structure guides

## Recommended install path

Clone or copy this repo into your OpenClaw workspace:

```bash
cd ~/.openclaw/workspace/skills
git clone <YOUR_REPO_URL> wechat-stock-analysis-blowout-v2
cd wechat-stock-analysis-blowout-v2
```

If the folder already exists, back it up first.

## Runtime requirements

- Node.js 22+
- Python 3.10+
- OpenClaw installed

Install Node dependencies:

```bash
npm install
```

Install Python dependencies:

```bash
python3 -m pip install --break-system-packages requests pandas tushare akshare==1.18.55
```

If your machine uses a virtualenv, install the same packages into that environment instead.

## Configuration

Copy the sample config:

```bash
cp .env.example .env
```

Then fill in your own values.

Important variables:

- `TUSHARE_TOKEN`
- `MODELSCOPE_TOKEN`
- `WECHAT_PUBLISH_PROFILE` or direct WeChat credentials
- `WECHAT_APP_ID`
- `WECHAT_APP_SECRET`

If you use a shared credential file flow, make sure `scripts/config.js` points to the correct file for your machine.

## WeChat publish notes

To publish drafts successfully, your server must satisfy both:

1. the correct `WECHAT_APP_ID` / `WECHAT_APP_SECRET`
2. the server IP added to the WeChat API whitelist

## Main command

For listed companies, run the full pipeline through `write_article.js`.

```bash
node scripts/write_article.js "泡泡玛特国际集团有限公司" "09992.HK" "/abs/path/to/output"
```

For A-shares:

```bash
node scripts/write_article.js "宁德时代" "300750.SZ" "/abs/path/to/output"
```

## Output

Typical output directory contains:

- `article.md`
- `article.html`
- `index.html`
- `financial_analysis.md`
- `financial_data.json`
- `company_profile_brief.md`
- `risk_brief.md`
- `external_risk_brief.md`
- `review.md`
- `generation_status.json`
- `images/`
- `*_wechat_package.zip`

## Operational notes

- Long runs should report progress via `generation_status.json`
- If review fails repeatedly, the pipeline may mark the article for manual review
- The pipeline can still package and publish even when manual review is recommended, depending on the branch taken

## Updating on another OpenClaw machine

Inside the skill directory:

```bash
git pull
npm install
python3 -m pip install --break-system-packages akshare==1.18.55
```

Then re-check:

- `.env`
- WeChat credential file
- IP whitelist
- OpenClaw skill routing
