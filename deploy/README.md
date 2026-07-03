# Deploying the bot to AWS

This replaces the original Lambda + EventBridge plan with a small always-on
server (AWS Lightsail). Why: Robinhood's official Trading MCP currently only
documents connecting through a live AI app session (Claude Code, Claude
Desktop, ChatGPT, Codex, Cursor, or Grok) — real-world reports say the
session needs to stay active for trades to go through. Lambda spins up fresh
and disappears after each run, so it can't reliably hold that kind of
connection open. A small always-on box can.

If Robinhood later documents a proper headless/service-account auth flow,
`main.py` doesn't need to change — only how it's invoked would (Lambda would
just call `main.run_cycle()` instead of a systemd timer).

None of this should be done until the Robinhood connection is confirmed
working locally first (see the main README's Robinhood section).

## What you're building

One small, cheap virtual machine that:
- Has the bot's code on it
- Runs `main.py` once per cycle, on a timer (like a repeating alarm clock)
- Keeps your secrets in AWS Secrets Manager, not on disk in plain text
- Texts/emails you via SNS on real trades, errors, or halts

## Step 1 — Create the Lightsail instance

1. Go to [lightsail.aws.amazon.com](https://lightsail.aws.amazon.com) and sign in with your AWS account (create one at aws.amazon.com if you don't have one — it asks for a credit card but the smallest instance here is about $3.50/month).
2. Click **Create instance**.
3. Choose a Linux/Unix platform, then the **OS Only → Ubuntu 22.04 LTS** blueprint.
4. Pick the cheapest plan (512 MB RAM is plenty for this bot).
5. Give it a name like `trading-bot`, and click **Create instance**.
6. Once it's running (takes ~1 minute), click on it, go to the **Connect** tab, and click **Connect using SSH** — this opens a terminal in your browser, already logged into the machine. You won't need to type any SSH commands yourself.

## Step 2 — Install dependencies on the instance

In the browser SSH terminal that just opened, paste this (one block, all at once):

```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git
sudo useradd -m -s /bin/bash tradingbot
sudo mkdir -p /opt/ai-trading-bot
sudo chown tradingbot:tradingbot /opt/ai-trading-bot
```

## Step 3 — Get the code onto the instance

Still in the browser SSH terminal:

```bash
sudo -u tradingbot git clone https://github.com/areebq50-ctrl/ai-trading-bot.git /opt/ai-trading-bot
cd /opt/ai-trading-bot
sudo -u tradingbot python3 -m venv venv
sudo -u tradingbot venv/bin/pip install -r requirements.txt
```

(If the repo is private, you'll be prompted for GitHub credentials — a
[personal access token](https://github.com/settings/tokens) works as the
password.)

## Step 4 — Store secrets in AWS Secrets Manager (not on the instance)

From your own computer (needs `aws configure` set up once with your AWS
account's access keys — the AWS Lightsail/IAM console will walk you through
generating those):

```bash
aws secretsmanager create-secret --name robinhood-trading-bot/robinhood-token \
  --secret-string "PASTE_YOUR_ROBINHOOD_ACCESS_TOKEN_HERE"
```

On the instance, create `/opt/ai-trading-bot/.env` (as the `tradingbot`
user) with the non-secret config plus a pointer to the secret:

```
DRY_RUN=true
MAX_CAPITAL=100
STRATEGY=mean_reversion
SYMBOLS=SPY
ROBINHOOD_TOKEN_SECRET_NAME=robinhood-trading-bot/robinhood-token
SNS_TOPIC_ARN=<created in step 6>
AWS_REGION=us-east-1
```

The instance also needs an IAM role attached (Lightsail → your instance →
**Networking → IAM role**, or via the AWS IAM console) with permission to
read that one secret and write to the two DynamoDB tables — nothing more.
This keeps the blast radius small if the instance itself is ever compromised.

## Step 5 — Create the DynamoDB tables

From your own computer:

```bash
bash deploy/create_dynamodb_tables.sh
```

## Step 6 — Set up notifications (SNS)

1. In the AWS Console, go to **SNS → Topics → Create topic** (type: Standard), name it `trading-bot-alerts`.
2. Click **Create subscription**, protocol **Email** (or **SMS** for texts), and enter where you want alerts sent.
3. Check your email/phone and confirm the subscription.
4. Copy the topic's ARN into `.env` as `SNS_TOPIC_ARN` (step 4).

## Step 7 — Schedule the bot

Back in the browser SSH terminal:

```bash
sudo cp /opt/ai-trading-bot/deploy/trading-bot.service /etc/systemd/system/
sudo cp /opt/ai-trading-bot/deploy/trading-bot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-bot.timer
```

Check it's scheduled: `systemctl list-timers trading-bot.timer`

Check a single run manually: `sudo -u tradingbot /opt/ai-trading-bot/venv/bin/python3 /opt/ai-trading-bot/main.py`

## The kill switch, deployed

The kill switch checks a local flag file by default (`risk.py`). On a
persistent instance, that's simplest: SSH in and run
`sudo -u tradingbot touch /opt/ai-trading-bot/kill_switch.flag` to pause the
bot instantly, `rm` it to resume. If you'd rather flip it without SSHing in,
swap `KillSwitch` to check a DynamoDB item instead — the interface in
`risk.py` is small enough to extend directly.
