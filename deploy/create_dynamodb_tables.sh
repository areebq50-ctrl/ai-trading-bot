#!/usr/bin/env bash
# Creates the two DynamoDB tables the bot needs. Run once, from a machine
# with the AWS CLI configured (aws configure) for your account.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"

echo "Creating trading-bot-logs (every decision, not just trades)..."
aws dynamodb create-table \
  --region "$REGION" \
  --table-name trading-bot-logs \
  --attribute-definitions AttributeName=id,AttributeType=S AttributeName=timestamp,AttributeType=S \
  --key-schema AttributeName=id,KeyType=HASH AttributeName=timestamp,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST

echo "Creating trading-bot-state (daily start-of-day balances, kill switch state)..."
aws dynamodb create-table \
  --region "$REGION" \
  --table-name trading-bot-state \
  --attribute-definitions AttributeName=key,AttributeType=S \
  --key-schema AttributeName=key,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

echo "Done. Tables are pay-per-request, so idle cost is ~\$0."
