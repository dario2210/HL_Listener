# Useful Queries

## Latest large trades

```sql
select
  trade_time,
  coin,
  price,
  size,
  notional_usd,
  buyer_address,
  seller_address
from hl_trades
order by trade_time desc
limit 20;
```

## Biggest trades

```sql
select
  trade_time,
  coin,
  notional_usd,
  buyer_address,
  seller_address
from hl_trades
order by notional_usd desc
limit 20;
```

## Most active wallets by notional

```sql
select
  wallet_address,
  wallet_role,
  count(*) as trade_count,
  sum(notional_usd) as total_notional_usd
from wallet_trade_events
group by wallet_address, wallet_role
order by total_notional_usd desc
limit 50;
```

## BTC only

```sql
select
  trade_time,
  coin,
  notional_usd,
  buyer_address,
  seller_address
from hl_trades
where coin = 'BTC'
order by trade_time desc
limit 20;
```

## ETH only

```sql
select
  trade_time,
  coin,
  notional_usd,
  buyer_address,
  seller_address
from hl_trades
where coin = 'ETH'
order by trade_time desc
limit 20;
```

## Wallet history

Podmien adres:

```sql
select
  trade_time,
  coin,
  wallet_role,
  notional_usd,
  hash
from wallet_trade_events
where wallet_address = '0x123'
order by trade_time desc
limit 100;
```

