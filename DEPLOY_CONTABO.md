# Deploy on Contabo

Ten plik opisuje najprostszy deployment na serwerze `Contabo` z uzyciem `docker compose`.

## 1. Logowanie na serwer

Z laptopa:

```bash
ssh user@twoj-serwer
```

## 2. Klon repo

Na serwerze:

```bash
git clone https://github.com/dario2210/HL_Listener.git
cd HL_Listener
```

## 3. Konfiguracja `.env`

Skopiuj szablon:

```bash
cp .env.example .env
```

Edytuj:

```bash
nano .env
```

Minimalne ustawienia na start:

```env
POSTGRES_DB=whale_listener
POSTGRES_USER=listener
POSTGRES_PASSWORD=tu_daj_mocne_haslo
DATABASE_URL=postgresql://listener:tu_daj_mocne_haslo@db:5432/whale_listener

TRACKED_COINS=BTC,ETH,SPX,cash:GOLD,cash:SILVER,cash:WTI
TRACKED_KEYWORDS=
MIN_NOTIONAL_USD=100000

CSV_EXPORT_DIR=/exports
CSV_EXPORT_INTERVAL_SECONDS=30

ADMINER_PORT=8080
ADMINER_BIND_ADDRESS=127.0.0.1
```

## 4. Start kontenerow

```bash
docker compose up -d --build
```

Sprawdzenie:

```bash
docker compose ps
```

## 5. Logi

Listener:

```bash
docker compose logs -f listener
```

CSV exporter:

```bash
docker compose logs -f csv-exporter
```

Adminer:

```bash
docker compose logs -f adminer
```

## 6. Gdzie znajdziesz dane

Pliki CSV:

```bash
ls -lah exports/
```

Podglad pliku:

```bash
head -n 20 exports/hl_trades.csv
```

Drugi plik:

```bash
head -n 20 exports/wallet_trade_events.csv
```

## 7. Adminer w przegladarce

Domyslnie `Adminer` jest dostepny tylko lokalnie na serwerze:

```text
127.0.0.1:8080
```

To oznacza, ze nie musisz otwierac go publicznie.

Z laptopa zrob tunel SSH:

```bash
ssh -L 8080:127.0.0.1:8080 user@twoj-serwer
```

Potem otworz:

```text
http://127.0.0.1:8080
```

Logowanie do `Adminer`:

- System: `PostgreSQL`
- Server: `db`
- Username: `listener` albo wartosc `POSTGRES_USER`
- Password: wartosc `POSTGRES_PASSWORD`
- Database: `whale_listener` albo wartosc `POSTGRES_DB`

## 8. Czy trzeba otwierac port na serwerze

Na start:

- `5432`: nie
- `8080`: nie, jesli uzywasz tunelu SSH

Czyli do bezpiecznego startu nie musisz otwierac zadnego nowego portu aplikacji.

Jedyne co musi byc otwarte to port SSH, zwykle `22`.

## 9. Jesli chcesz publiczny Adminer

To rozwiazanie mniej bezpieczne i lepiej go unikac.

Jesli mimo to chcesz:

1. W `.env` ustaw:

```env
ADMINER_BIND_ADDRESS=0.0.0.0
```

2. Otworz port `8080/tcp` w firewallu.

3. Najlepiej ogranicz dostep tylko do swojego IP.

## 10. Restart i aktualizacja

Po zmianach w kodzie:

```bash
git pull
docker compose up -d --build
```

Restart bez rebuilda:

```bash
docker compose restart
```

## 11. Zatrzymanie

```bash
docker compose down
```

Jesli chcesz zatrzymac kontenery, ale zachowac dane bazy:

- to normalnie `docker compose down`
- nie usuwaj volume `postgres_data`

## 12. Szybki smoke test

Po kilku minutach od startu:

```bash
docker compose exec db psql -U listener -d whale_listener -c "select count(*) from hl_trades;"
```

Ostatnie transakcje:

```bash
docker compose exec db psql -U listener -d whale_listener -c "select trade_time, coin, notional_usd, buyer_address, seller_address from hl_trades order by trade_time desc limit 10;"
```
