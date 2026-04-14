# Hyperliquid Whale Listener

Kontenerowy listener `Hyperliquid`, ktory nasluchuje wybranych rynkow perpetual:

- pobiera liste rynkow perpetual z oficjalnego API,
- subskrybuje strumien `trades` po WebSocket,
- filtruje tylko transakcje powyzej zadanego progu USD,
- zapisuje rekordy do `PostgreSQL`,
- przygotowuje widok SQL pod pozniejsza analize adresow,
- eksportuje dane do `CSV`,
- udostepnia `Adminer` do podgladu bazy w przegladarce.

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

5. Eksportowane pliki CSV pojawia sie w katalogu:

```bash
./exports
```

6. Dokladna instrukcja wdrozenia na serwer jest w:

```text
DEPLOY_CONTABO.md
```

7. Przykladowe zapytania SQL sa w:

```text
QUERIES.md
```

## Konfiguracja

Najwazniejsze zmienne w `.env`:

- `TRACKED_COINS=BTC,ETH,xyz:SP500,xyz:GOLD,xyz:SILVER,cash:WTI`
- `TRACKED_KEYWORDS=`
- `MIN_NOTIONAL_USD=100000`
- `CSV_EXPORT_INTERVAL_SECONDS=30`
- `ADMINER_PORT=8080`
- `ADMINER_BIND_ADDRESS=127.0.0.1`

Jak dziala wybor rynkow:

- `TRACKED_COINS` dopasowuje dokladne nazwy rynku, np. `BTC`, `ETH`, `xyz:SP500`, `xyz:GOLD`.
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

## CSV export

Osobny kontener `csv-exporter` odswieza pliki:

- `exports/hl_trades.csv`
- `exports/wallet_trade_events.csv`

Eksport jest robiony cyklicznie co `CSV_EXPORT_INTERVAL_SECONDS`.

## Adminer

`Adminer` pozwala obejrzec baze w przegladarce.

Domyslnie nasluchuje tylko lokalnie na serwerze:

```text
127.0.0.1:8080
```

Logowanie:

- System: `PostgreSQL`
- Server: `db`
- Username: wartosc `POSTGRES_USER`
- Password: wartosc `POSTGRES_PASSWORD`
- Database: wartosc `POSTGRES_DB`

Najbezpieczniejszy sposob dostepu z laptopa to tunel SSH, np.:

```bash
ssh -L 8080:127.0.0.1:8080 user@twoj-serwer
```

Wtedy otwierasz w przegladarce:

```text
http://127.0.0.1:8080
```

## Uwagi operacyjne

- Ten MVP nasluchuje rynki `perp`.
- Dla `gold`, `silver` i `SP500` listener jest ustawiony na najbardziej plynne obecnie builder-deployed rynki `xyz:GOLD`, `xyz:SILVER`, `xyz:SP500`.
- Dla ropy `WTI` listener obserwuje rynek `cash:WTI`.
- Gdy bedziemy gotowi rozszerzyc system o inne aktywa, wystarczy dopisac je do `TRACKED_COINS` albo `TRACKED_KEYWORDS`.
- Jesli chcesz pozniej dodac alerty, najprosciej dolozyc osobny worker Telegram/Discord nad ta sama baza.
- Nie trzeba wystawiac portu `5432` do internetu. `Adminer` laczy sie z baza po sieci Dockera.
- Jesli chcesz wystawic `Adminer` publicznie, zmien `ADMINER_BIND_ADDRESS=0.0.0.0` i otworz port `ADMINER_PORT` w firewallu. Lepiej ograniczyc dostep do Twojego IP.
