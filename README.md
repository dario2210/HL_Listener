# Hyperliquid Whale Listener

Kontenerowy listener `Hyperliquid`, ktory na start nasluchuje tylko `BTC` i `ETH`:

- pobiera liste rynkow perpetual z oficjalnego API,
- subskrybuje strumien `trades` po WebSocket,
- filtruje tylko transakcje powyzej zadanego progu USD,
- zapisuje rekordy do `PostgreSQL`,
- przygotowuje widok SQL pod pozniejsza analize adresow.

## Co zapisuje baza

Kazdy trade zawiera m.in.:

- czas transakcji UTC,
- instrument,
- cene,
- wielkosc,
- wartosc nominalna USD,
- `hash`,
- `tid`,
- adres kupujacego,
- adres sprzedajacego,
- pelny `raw_payload` z websocketu.

## Szybki start lokalnie / na Contabo

1. Skopiuj konfiguracje:

```bash
cp .env.example .env
```

2. Ustaw wlasne haslo w `POSTGRES_PASSWORD` oraz zaktualizuj `DATABASE_URL`.

3. Uruchom stack:

```bash
docker compose up -d --build
```

4. Podejrzyj logi:

```bash
docker compose logs -f listener
```

## Konfiguracja

Najwazniejsze zmienne w `.env`:

- `TRACKED_COINS=BTC,ETH`
- `TRACKED_KEYWORDS=`
- `MIN_NOTIONAL_USD=100000`

Jak dziala wybor rynkow:

- `TRACKED_COINS` dopasowuje dokladne nazwy rynku, np. `BTC`, `ETH`.
- `TRACKED_KEYWORDS` jest opcjonalne i na start moze zostac puste.
- To jest przydatne dla builder-deployed perps, gdzie nazwa moze miec prefiks typu `dex:ASSET`.

## Analiza adresow

Listener tworzy widok `wallet_trade_events`, ktory zamienia kazdy trade na dwa rekordy:

- `buyer`
- `seller`

To ulatwia pozniejsze zapytania typu:

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

## Uwagi operacyjne

- Ten MVP nasluchuje rynki `perp`.
- Gdy bedziemy gotowi rozszerzyc system o inne aktywa, wystarczy dopisac je do `TRACKED_COINS` albo `TRACKED_KEYWORDS`.
- Jesli chcesz pozniej dodac alerty, najprosciej dolozyc osobny worker Telegram/Discord nad ta sama baza.
