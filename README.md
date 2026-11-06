# Dream Station preorder Telegram watcher

A personal, **free-to-host** (in a public GitHub repository) watcher for https://dreamstation.com.sa/pre-orders/c630489014.

- Attempts checks **every 10 minutes** using scheduled GitHub Actions. GitHub may delay or skip a scheduled execution; this is **not** guaranteed instant monitoring.
- **First priority:** explicitly marked Dream Station exclusives / special exclusive variants, bundles and SteelBooks.
- **Second:** collector's/limited/deluxe editions and SteelBooks.
- **Third:** other newly added preorder games, on any platform.
- Sends a Telegram message containing name, visible price, available stock indicator if determinable, and direct link.
- Alerts if an item previously observed as out of stock becomes available again.
- Saves `state.json` in the repository to avoid duplicate alerts.
- **First run is silent** and establishes the current catalog baseline. Run the optional test action to verify Telegram.

## Set up once (about five steps)

1. In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, pick a name and unique username (ending in `bot`). Save the **bot token** privately. Do not send it in a public chat or commit it to GitHub.
2. Open your newly created bot and send `/start`. To find your personal `chat_id`, privately open `https://api.telegram.org/botYOUR_TOKEN/getUpdates` in your browser (replace `YOUR_TOKEN` with your token). Find the `"chat":{"id":...}` value. The bot needs a message from you first. If result is empty, send another message to your bot and refresh. Treat the token-bearing URL as sensitive and close the tab afterward.
3. Create a **public** GitHub repository (public GitHub Actions runtime is free). Upload these files **preserving the `.github/workflows/` folder**. A public repo makes code and `state.json` public, but never your bot token because you will store the token as a secret. If you prefer a private repository, your GitHub plan's Actions-minute allowance applies.
4. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**. Add **`TELEGRAM_BOT_TOKEN`** and **`TELEGRAM_CHAT_ID`**. Never put their real values in the source files or the repository description.
5. Open **Actions → Dream Station Preorder Alerts → Run workflow**, select `test_message = true` and run. You should receive a Telegram confirmation. Then run again with `test_message = false` to initialize the baseline. Scheduled checks should then run every 10 minutes, subject to GitHub scheduling delays.

## How alerts work

**Urgent example**

    🔴 URGENT — DREAM STATION EXCLUSIVE
    🎮 [New exclusive game / edition]
    💰 Price: 399 SAR
    📦 Availability: Available to order
    🔗 OPEN DREAM STATION PREORDER

Prices and availability are only displayed when the page provides them. `Not verified` means the bot could not confirm stock. A product mentioned on another page is not automatically treated as a new preorder.

## Real-world limitations

- Dream Station is a **JavaScript-rendered Salla storefront**. The monitor reads rendered product cards and common Salla product JSON fields. Storefront redesigns, anti-bot protection, and different pagination behavior may require adjustments. **This code has not been live-tested against Dream Station's JavaScript pages.**
- A newly added listing must be visible in the monitored category for the bot to find it; unpublished or hidden items cannot be detected. The bot checks up to 4 category pages. Depending on how their shop handles pagination, some products may not be included.
- The first successful scan stores the baseline and does not announce every existing product. New additions are announced on subsequent scans.
- GitHub scheduled workflows can run late or occasionally be dropped. **Public repository schedules may be disabled after 60 days without qualifying activity.** Check the Actions tab occasionally.
- GitHub may change free-tier rules and runners. A public repository exposes the list of products saved in `state.json`, but not the GitHub Actions secrets.
- This is an alert bot, not an automatic preorder purchaser; you buy on Dream Station yourself.

## Troubleshooting

- `No product cards found`: Dream Station's page structure has changed or is blocking automation. No fake alerts will be sent or baseline overwritten; inspect Actions logs.
- `Forbidden / chat not found`: open the bot in Telegram and send `/start`; verify `TELEGRAM_CHAT_ID`.
- `Unauthorized`: replace the bot token GitHub secret with the correct one.
- `Resource not accessible by integration` when committing state: make sure the repo Actions permissions allow writing; under **Settings → Actions → General → Workflow permissions**, allow **Read and write permissions** if your organization permits it.
- Telegram messages don't arrive: run a manual `test_message = true` workflow from the Actions tab.

## Run locally (optional)

Install Python 3.12+, then `pip install -r requirements.txt` and `python -m playwright install chromium`. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as environment variables, then `python monitor.py`.
