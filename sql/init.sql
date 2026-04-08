create table if not exists hl_trades (
    id bigserial primary key,
    venue text not null default 'hyperliquid',
    dex text not null default '',
    coin text not null,
    trade_time timestamptz not null,
    ingest_time timestamptz not null default now(),
    side text not null,
    price numeric(38, 18) not null,
    size numeric(38, 18) not null,
    notional_usd numeric(38, 18) not null,
    hash text not null,
    tid bigint not null,
    buyer_address text not null,
    seller_address text not null,
    raw_payload jsonb not null,
    constraint hl_trades_trade_key unique (dex, coin, trade_time, tid)
);

create index if not exists hl_trades_trade_time_idx
    on hl_trades (trade_time desc);

create index if not exists hl_trades_coin_trade_time_idx
    on hl_trades (coin, trade_time desc);

create index if not exists hl_trades_notional_idx
    on hl_trades (notional_usd desc);

create index if not exists hl_trades_buyer_address_idx
    on hl_trades (buyer_address);

create index if not exists hl_trades_seller_address_idx
    on hl_trades (seller_address);

create or replace view wallet_trade_events as
select
    id as trade_row_id,
    venue,
    dex,
    coin,
    trade_time,
    ingest_time,
    side,
    price,
    size,
    notional_usd,
    hash,
    tid,
    'buyer'::text as wallet_role,
    buyer_address as wallet_address
from hl_trades

union all

select
    id as trade_row_id,
    venue,
    dex,
    coin,
    trade_time,
    ingest_time,
    side,
    price,
    size,
    notional_usd,
    hash,
    tid,
    'seller'::text as wallet_role,
    seller_address as wallet_address
from hl_trades;

