-- ========== ENUMS ==========
CREATE TYPE movement_type AS ENUM (
  'receive','sale','transfer_out','transfer_in','return_in','adjustment','damage'
);
CREATE TYPE order_status AS ENUM
  ('pending','queued','processing','confirmed','failed','cancelled','completed','returned');
CREATE TYPE transfer_status AS ENUM ('requested','in_transit','received','cancelled');
CREATE TYPE return_status   AS ENUM ('requested','approved','received','rejected');

-- ========== MASTER DATA ==========
CREATE TABLE users (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  email       TEXT UNIQUE NOT NULL,
  role        TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer','staff','admin')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE warehouses (
  id          BIGSERIAL PRIMARY KEY,
  code        TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  address     TEXT,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE products (
  id          BIGSERIAL PRIMARY KEY,
  sku         TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  price       NUMERIC(14,2) NOT NULL DEFAULT 0,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== CURRENT STOCK ==========
CREATE TABLE stock_levels (
  product_id    BIGINT NOT NULL REFERENCES products(id),
  warehouse_id  BIGINT NOT NULL REFERENCES warehouses(id),
  on_hand       INT NOT NULL DEFAULT 0 CHECK (on_hand >= 0),
  reserved      INT NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (product_id, warehouse_id),
  CHECK (reserved <= on_hand)
);

-- ========== LEDGER (append-only) ==========
CREATE TABLE stock_movements (
  id               BIGSERIAL PRIMARY KEY,
  product_id       BIGINT NOT NULL REFERENCES products(id),
  warehouse_id     BIGINT NOT NULL REFERENCES warehouses(id),
  movement_type    movement_type NOT NULL,
  qty_change       INT NOT NULL CHECK (qty_change <> 0),
  on_hand_after    INT NOT NULL,
  reference_type   TEXT,
  reference_id     BIGINT,
  user_id          BIGINT REFERENCES users(id),
  note             TEXT,
  idempotency_key  TEXT UNIQUE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mov_product   ON stock_movements (product_id, created_at DESC);
CREATE INDEX idx_mov_warehouse ON stock_movements (warehouse_id, created_at DESC);
CREATE INDEX idx_mov_ref       ON stock_movements (reference_type, reference_id);

CREATE FUNCTION forbid_ledger_change() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'stock_movements is append-only'; END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_immutable
BEFORE UPDATE OR DELETE ON stock_movements
FOR EACH ROW EXECUTE FUNCTION forbid_ledger_change();

-- ========== ORDERS ==========
CREATE TABLE orders (
  id             BIGSERIAL PRIMARY KEY,
  order_no       TEXT UNIQUE NOT NULL,
  user_id        BIGINT NOT NULL REFERENCES users(id),
  warehouse_id   BIGINT NOT NULL REFERENCES warehouses(id),
  status         order_status NOT NULL DEFAULT 'pending',
  queue_job_id   TEXT,
  fail_reason    TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at   TIMESTAMPTZ
);
CREATE INDEX idx_orders_user   ON orders (user_id, created_at DESC);
CREATE INDEX idx_orders_status ON orders (status);

CREATE TABLE order_items (
  id          BIGSERIAL PRIMARY KEY,
  order_id    BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id  BIGINT NOT NULL REFERENCES products(id),
  qty         INT NOT NULL CHECK (qty > 0),
  unit_price  NUMERIC(14,2) NOT NULL
);

CREATE TABLE order_status_history (
  id          BIGSERIAL PRIMARY KEY,
  order_id    BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  from_status order_status,
  to_status   order_status NOT NULL,
  changed_by  BIGINT REFERENCES users(id),
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========== TRANSFERS ==========
CREATE TABLE transfers (
  id               BIGSERIAL PRIMARY KEY,
  transfer_no      TEXT UNIQUE NOT NULL,
  from_warehouse   BIGINT NOT NULL REFERENCES warehouses(id),
  to_warehouse     BIGINT NOT NULL REFERENCES warehouses(id),
  status           transfer_status NOT NULL DEFAULT 'requested',
  requested_by     BIGINT REFERENCES users(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  shipped_at       TIMESTAMPTZ,
  received_at      TIMESTAMPTZ,
  CHECK (from_warehouse <> to_warehouse)
);

CREATE TABLE transfer_items (
  id           BIGSERIAL PRIMARY KEY,
  transfer_id  BIGINT NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
  product_id   BIGINT NOT NULL REFERENCES products(id),
  qty          INT NOT NULL CHECK (qty > 0)
);

-- ========== RETURNS ==========
CREATE TABLE returns (
  id            BIGSERIAL PRIMARY KEY,
  return_no     TEXT UNIQUE NOT NULL,
  order_id      BIGINT NOT NULL REFERENCES orders(id),
  warehouse_id  BIGINT NOT NULL REFERENCES warehouses(id),
  status        return_status NOT NULL DEFAULT 'requested',
  reason        TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  received_at   TIMESTAMPTZ
);

CREATE TABLE return_items (
  id          BIGSERIAL PRIMARY KEY,
  return_id   BIGINT NOT NULL REFERENCES returns(id) ON DELETE CASCADE,
  product_id  BIGINT NOT NULL REFERENCES products(id),
  qty         INT NOT NULL CHECK (qty > 0),
  restock     BOOLEAN NOT NULL DEFAULT TRUE
);

-- ========== THE ONE FUNCTION THAT CHANGES STOCK ==========
CREATE OR REPLACE FUNCTION apply_stock_movement(
  p_product_id   BIGINT,
  p_warehouse_id BIGINT,
  p_type         movement_type,
  p_qty_change   INT,
  p_ref_type     TEXT,
  p_ref_id       BIGINT,
  p_user_id      BIGINT,
  p_idem_key     TEXT,
  p_note         TEXT DEFAULT NULL
) RETURNS INT AS $$
DECLARE
  v_after INT;
BEGIN
  IF p_idem_key IS NOT NULL THEN
    SELECT on_hand_after INTO v_after FROM stock_movements WHERE idempotency_key = p_idem_key;
    IF FOUND THEN RETURN v_after; END IF;
  END IF;

  INSERT INTO stock_levels (product_id, warehouse_id)
  VALUES (p_product_id, p_warehouse_id)
  ON CONFLICT DO NOTHING;

  UPDATE stock_levels
     SET on_hand = on_hand + p_qty_change, updated_at = now()
   WHERE product_id = p_product_id
     AND warehouse_id = p_warehouse_id
     AND on_hand + p_qty_change >= reserved
  RETURNING on_hand INTO v_after;

  IF v_after IS NULL THEN
    RAISE EXCEPTION 'INSUFFICIENT_STOCK' USING ERRCODE = 'P0001';
  END IF;

  INSERT INTO stock_movements
    (product_id, warehouse_id, movement_type, qty_change, on_hand_after,
     reference_type, reference_id, user_id, note, idempotency_key)
  VALUES
    (p_product_id, p_warehouse_id, p_type, p_qty_change, v_after,
     p_ref_type, p_ref_id, p_user_id, p_note, p_idem_key);

  RETURN v_after;
END; $$ LANGUAGE plpgsql;
