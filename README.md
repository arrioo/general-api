# general-api

FastAPI + PostgreSQL (ledger-based stock) + BullMQ queue + Node.js worker, managed by PM2.
Requires Python 3.10+, Node 18+, PostgreSQL, Redis.

## Setup

```bash
cp .env.example .env                      # edit DATABASE_URL
createdb general
psql "$DATABASE_URL" -f sql/01_schema.sql
psql "$DATABASE_URL" -f sql/02_seed.sql

python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
(cd worker && npm install)

pm2 start ecosystem.config.js && pm2 save
```

Docs: http://localhost:8000/docs (click "Authorize"-less: just add header `X-User-Id`).

## Demo auth

Header `X-User-Id` picks the user (DEMO ONLY, replace with JWT in `app/deps.py`).

| id  | role     | name           |
| --- | -------- | -------------- |
| 1   | admin    | Admin Utama    |
| 2   | staff    | Budi Gudang    |
| 3-5 | customer | Siti/Andi/Dewi |

Warehouses: 1=JKT, 2=SBY, 3=BDG. Products: 1=Laptop, 2=Smartphone (only 5 in JKT), 3=Headphone,
4=Keyboard, 5=Mouse, 6=Monitor, 7=Charger, 8=Webcam.

Permissions: customers create/cancel/view their own orders and request returns.
Staff/admin: stock receive/adjust/movements, transfers, receive returns.

## Try it

```bash
H='Content-Type: application/json'

# 1. order 2 mice from Jakarta as Siti (queued -> worker -> confirmed)
curl -X POST localhost:8000/orders -H "$H" -H "X-User-Id: 3" \
  -d '{"warehouse_id":1,"items":[{"product_id":5,"qty":2}]}'
curl localhost:8000/orders/1 -H "X-User-Id: 3"
curl localhost:8000/orders/1/history -H "X-User-Id: 3"

# 2. out-of-stock test: 6 smartphones when JKT only has 5 -> status failed
curl -X POST localhost:8000/orders -H "$H" -H "X-User-Id: 4" \
  -d '{"warehouse_id":1,"items":[{"product_id":2,"qty":6}]}'

# 3. stock + ledger (staff)
curl "localhost:8000/stock?product_id=5" -H "X-User-Id: 2"
curl "localhost:8000/stock/movements?product_id=5" -H "X-User-Id: 2"

# 4. receive + adjust
curl -X POST localhost:8000/stock/receive -H "$H" -H "X-User-Id: 2" \
  -d '{"product_id":2,"warehouse_id":1,"qty":20,"note":"PO-1001"}'
curl -X POST localhost:8000/stock/adjust -H "$H" -H "X-User-Id: 2" \
  -d '{"product_id":5,"warehouse_id":1,"qty_change":-1,"note":"1 unit broken, stock opname"}'

# 5. transfer JKT -> BDG
curl -X POST localhost:8000/transfers -H "$H" -H "X-User-Id: 2" \
  -d '{"from_warehouse":1,"to_warehouse":3,"items":[{"product_id":3,"qty":10}]}'
curl -X POST localhost:8000/transfers/1/ship    -H "X-User-Id: 2"
curl -X POST localhost:8000/transfers/1/receive -H "X-User-Id: 2"

# 6. return 1 mouse from order 1, then receive (add "items":[{"product_id":5,"restock":false}] if damaged)
curl -X POST localhost:8000/returns -H "$H" -H "X-User-Id: 3" \
  -d '{"order_id":1,"reason":"Defective","items":[{"product_id":5,"qty":1}]}'
curl -X POST localhost:8000/returns/1/receive -H "X-User-Id: 2"
```

## Audit check (ledger must equal current stock; expect 0 rows)

```sql
SELECT s.product_id, s.warehouse_id, s.on_hand, COALESCE(SUM(m.qty_change),0) AS ledger_total
FROM stock_levels s
LEFT JOIN stock_movements m USING (product_id, warehouse_id)
GROUP BY s.product_id, s.warehouse_id, s.on_hand
HAVING s.on_hand <> COALESCE(SUM(m.qty_change),0);
```
