-- ========== USERS ==========
INSERT INTO users (id, name, email, role) VALUES
 (1, 'Admin Utama',   'admin@example.com', 'admin'),
 (2, 'Budi Gudang',   'staff@example.com', 'staff'),
 (3, 'Siti Customer', 'siti@example.com',  'customer'),
 (4, 'Andi Customer', 'andi@example.com',  'customer'),
 (5, 'Dewi Customer', 'dewi@example.com',  'customer');

-- ========== WAREHOUSES ==========
INSERT INTO warehouses (id, code, name, address) VALUES
 (1, 'JKT', 'Gudang Jakarta',   'Jl. Daan Mogot, Jakarta Barat'),
 (2, 'SBY', 'Gudang Surabaya',  'Rungkut Industri, Surabaya'),
 (3, 'BDG', 'Gudang Bandung',   'Jl. Soekarno-Hatta, Bandung');

-- ========== PRODUCTS ==========
INSERT INTO products (id, sku, name, price) VALUES
 (1, 'LPT-001', 'Laptop 14 inch',        12500000),
 (2, 'PHN-001', 'Smartphone X',           6500000),
 (3, 'HDP-001', 'Wireless Headphone',      850000),
 (4, 'KBD-001', 'Mechanical Keyboard',     750000),
 (5, 'MSE-001', 'Wireless Mouse',          250000),
 (6, 'MON-001', 'Monitor 24 inch',        1900000),
 (7, 'CHG-001', 'USB-C Charger 65W',       350000),
 (8, 'CAM-001', 'Webcam HD',               480000);

-- keep sequences in sync after explicit ids
SELECT setval(pg_get_serial_sequence('users','id'),      (SELECT max(id) FROM users));
SELECT setval(pg_get_serial_sequence('warehouses','id'), (SELECT max(id) FROM warehouses));
SELECT setval(pg_get_serial_sequence('products','id'),   (SELECT max(id) FROM products));

-- ========== INITIAL STOCK (goes through the ledger, done by staff user 2) ==========
SELECT apply_stock_movement(
         p.id, w.id, 'receive', v.qty, 'manual', NULL, 2,
         'seed-' || p.sku || '-' || w.code, 'Initial stock')
FROM (VALUES
  ('LPT-001','JKT',50), ('LPT-001','SBY',20), ('LPT-001','BDG',10),
  ('PHN-001','JKT', 5),                                   -- tiny stock: test out_of_stock
  ('PHN-001','SBY',30),
  ('HDP-001','JKT',100),('HDP-001','BDG',40),
  ('KBD-001','JKT',60), ('KBD-001','SBY',25),
  ('MSE-001','JKT',200),('MSE-001','SBY',80),('MSE-001','BDG',50),
  ('MON-001','JKT',15), ('MON-001','BDG',15),
  ('CHG-001','JKT',150),('CHG-001','SBY',60),
  ('CAM-001','JKT',35), ('CAM-001','SBY',10)
) AS v(sku, wh, qty)
JOIN products   p ON p.sku  = v.sku
JOIN warehouses w ON w.code = v.wh;
