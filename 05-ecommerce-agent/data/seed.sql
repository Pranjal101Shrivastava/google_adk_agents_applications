-- Seed data for the E-Commerce Agent storefront.
--
-- Chosen to exercise the behaviours the agent must get right:
--   * A product that is out of stock everywhere, so "check before promising"
--     has something to catch.
--   * A variant priced differently from its siblings, so a total cannot be
--     guessed from the product.
--   * Two customers, so a cross-account query has something to wrongly reach
--     for and be refused.
--   * An order in every interesting status, including one not cancellable.
--
--   psql "$DATABASE_URL" -f data/seed.sql

-- --------------------------------------------------------------------------
-- Customers
-- --------------------------------------------------------------------------
INSERT INTO users (email, full_name) VALUES
  ('alex.morgan@example.com', 'Alex Morgan'),
  ('priya.raman@example.com', 'Priya Raman')
ON CONFLICT (email) DO NOTHING;

-- --------------------------------------------------------------------------
-- Products
-- --------------------------------------------------------------------------
INSERT INTO products (name, brand, category, description, popularity_score) VALUES
  ('Nimbus 25', 'ASICS', 'Running',
   'Flagship cushioned running shoe with FF BLAST+ foam and a TRUSSTIC stability system. Built for high-mileage daily training on road.',
   9.20),
  ('Glycerin 20', 'Brooks', 'Running',
   'Plush neutral running shoe with DNA LOFT v3 nitrogen-infused cushioning. Soft landings for recovery runs and long easy miles.',
   8.80),
  ('Speedgoat 5', 'HOKA', 'Trail Running',
   'Aggressive trail runner with a Vibram Megagrip outsole and 5mm lugs. Grips wet rock and loose gravel on technical descents.',
   9.00),
  ('Peregrine 13', 'Saucony', 'Trail Running',
   'Lightweight trail shoe with PWRTRAC rubber and a rock plate. Fast on hardpack, protective underfoot on rough ground.',
   8.10),
  ('Trek 500', 'Quechua', 'Hiking',
   'Versatile 50-litre hiking backpack with an adjustable suspension system. Sized for weekend trips and multi-day routes.',
   7.60),
  ('Atmos AG 65', 'Osprey', 'Hiking',
   'Premium 65-litre backpack with the Anti-Gravity suspension system. Carries heavy loads comfortably over long distances.',
   8.90),
  ('Baltoro 75', 'Gregory', 'Hiking',
   'Expedition-ready 75-litre pack with a Response A3 suspension system. Built for heavy loads on extended backpacking trips.',
   8.30),
  ('Thermal Base Layer', 'Icebreaker', 'Apparel',
   'Merino wool long-sleeve base layer. Regulates temperature and resists odour on multi-day trips in cold conditions.',
   7.90),
  ('Storm Shell Jacket', 'Arc''teryx', 'Apparel',
   'Three-layer waterproof hardshell with a GORE-TEX membrane and pit zips. Holds up in sustained wind-driven rain.',
   9.40),
  ('Court Pro 3', 'Nike', 'Tennis',
   'Stable tennis shoe with a herringbone outsole and lateral support for hard-court play.',
   7.20)
ON CONFLICT (name) DO NOTHING;

-- --------------------------------------------------------------------------
-- Variants
-- --------------------------------------------------------------------------
-- Note the Nimbus 25 in EU 48: priced €10 above its siblings. A model that
-- guesses the total from the product page gets this wrong, which is the point.
INSERT INTO product_variants (product_id, size, color, price, sku)
SELECT p.product_id, v.size, v.color, v.price,
       UPPER(LEFT(REPLACE(p.name, ' ', ''), 6)) || '-' || v.size || '-' || LEFT(v.color, 3)
FROM products p
JOIN (VALUES
  ('Nimbus 25',           '41', 'Black/Gold',     169.99),
  ('Nimbus 25',           '42', 'Black/Gold',     169.99),
  ('Nimbus 25',           '43', 'Black/Gold',     169.99),
  ('Nimbus 25',           '43', 'White/Blue',     169.99),
  ('Nimbus 25',           '48', 'Black/Gold',     179.99),
  ('Glycerin 20',         '42', 'Grey/Orange',    159.95),
  ('Glycerin 20',         '43', 'Grey/Orange',    159.95),
  ('Glycerin 20',         '44', 'Navy/White',     159.95),
  ('Speedgoat 5',         '42', 'Castlerock',     144.99),
  ('Speedgoat 5',         '43', 'Castlerock',     144.99),
  ('Speedgoat 5',         '44', 'Blue Coral',     144.99),
  ('Peregrine 13',        '43', 'Black/Citron',   129.00),
  ('Peregrine 13',        '44', 'Black/Citron',   129.00),
  ('Trek 500',            'One Size', 'Khaki',     79.99),
  ('Atmos AG 65',         'M',  'Black',          269.00),
  ('Atmos AG 65',         'L',  'Black',          269.00),
  ('Baltoro 75',          'M',  'Dusty Blue',     299.95),
  ('Thermal Base Layer',  'S',  'Charcoal',        89.99),
  ('Thermal Base Layer',  'M',  'Charcoal',        89.99),
  ('Thermal Base Layer',  'L',  'Charcoal',        89.99),
  ('Storm Shell Jacket',  'M',  'Black',          549.00),
  ('Storm Shell Jacket',  'L',  'Black',          549.00),
  ('Court Pro 3',         '42', 'White/Navy',     119.99),
  ('Court Pro 3',         '43', 'White/Navy',     119.99)
) AS v(product_name, size, color, price) ON v.product_name = p.name
ON CONFLICT (product_id, size, color) DO NOTHING;

-- --------------------------------------------------------------------------
-- Stores
-- --------------------------------------------------------------------------
INSERT INTO stores (name, address, city, country, latitude, longitude) VALUES
  ('GenAI Sports Amsterdam Centre', 'Kalverstraat 112', 'Amsterdam', 'Netherlands', 52.370216, 4.895168),
  ('GenAI Sports Amsterdam Zuid',   'Gelderlandplein 45', 'Amsterdam', 'Netherlands', 52.333611, 4.871389),
  ('GenAI Sports Utrecht',          'Oudegracht 210', 'Utrecht', 'Netherlands', 52.090737, 5.121420),
  ('GenAI Sports Rotterdam',        'Lijnbaan 88', 'Rotterdam', 'Netherlands', 51.922500, 4.479200),
  ('GenAI Sports Berlin Mitte',     'Friedrichstrasse 140', 'Berlin', 'Germany', 52.520008, 13.404954)
ON CONFLICT (name) DO NOTHING;

-- --------------------------------------------------------------------------
-- Inventory
-- --------------------------------------------------------------------------
-- Every variant gets stock in the first three stores...
INSERT INTO inventory (store_id, variant_id, quantity)
SELECT s.store_id, v.variant_id,
       CASE WHEN (v.variant_id + s.store_id) % 4 = 0 THEN 0 ELSE 2 + (v.variant_id % 7) END
FROM stores s
CROSS JOIN product_variants v
WHERE s.name IN (
  'GenAI Sports Amsterdam Centre',
  'GenAI Sports Amsterdam Zuid',
  'GenAI Sports Utrecht'
)
ON CONFLICT (store_id, variant_id) DO NOTHING;

-- ...except the Storm Shell Jacket, which is out of stock everywhere. The
-- agent must check rather than assume, and this is what catches it if it does.
UPDATE inventory SET quantity = 0
WHERE variant_id IN (
  SELECT v.variant_id FROM product_variants v
  JOIN products p ON p.product_id = v.product_id
  WHERE p.name = 'Storm Shell Jacket'
);

-- --------------------------------------------------------------------------
-- Delivery methods
-- --------------------------------------------------------------------------
INSERT INTO delivery_methods (name, description, cost, estimated_days) VALUES
  ('Standard',       'Delivered by courier to your address.',                   4.99, '3-5 working days'),
  ('Express',        'Next working day when ordered before 15:00.',            12.99, '1 working day'),
  ('Click & Collect','Collect from any GenAI Sports store, free of charge.',     0.00, '2-3 working days'),
  ('Saturday',       'Weekend delivery to your address.',                       17.50, 'Next Saturday')
ON CONFLICT (name) DO NOTHING;

-- --------------------------------------------------------------------------
-- A basket for Alex
-- --------------------------------------------------------------------------
INSERT INTO shopping_lists (user_id)
SELECT user_id FROM users WHERE email = 'alex.morgan@example.com'
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO shopping_list_items (list_id, variant_id, quantity)
SELECT l.list_id, v.variant_id, 1
FROM shopping_lists l
JOIN users u ON u.user_id = l.user_id
JOIN product_variants v ON v.sku IS NOT NULL
JOIN products p ON p.product_id = v.product_id
WHERE u.email = 'alex.morgan@example.com'
  AND p.name = 'Speedgoat 5' AND v.size = '43' AND v.color = 'Castlerock'
ON CONFLICT (list_id, variant_id) DO NOTHING;

-- --------------------------------------------------------------------------
-- Orders - one in each interesting status
-- --------------------------------------------------------------------------
INSERT INTO orders (user_id, store_id, method_id, status, total_amount, shipping_address, created_at)
SELECT u.user_id, s.store_id, d.method_id, o.status, o.total, o.address, NOW() - o.age
FROM (VALUES
  ('alex.morgan@example.com', 'GenAI Sports Amsterdam Centre', 'Standard',        'Delivered', 174.98, 'Prinsengracht 263, 1016 GV Amsterdam', INTERVAL '21 days'),
  ('alex.morgan@example.com', 'GenAI Sports Amsterdam Centre', 'Express',         'Shipped',   159.95, 'Prinsengracht 263, 1016 GV Amsterdam', INTERVAL '2 days'),
  ('alex.morgan@example.com', 'GenAI Sports Amsterdam Zuid',   'Click & Collect', 'Confirmed', 129.00, 'Collect in store',                     INTERVAL '6 hours'),
  ('priya.raman@example.com', 'GenAI Sports Utrecht',          'Standard',        'Packed',    269.00, 'Oudegracht 12, 3511 AL Utrecht',       INTERVAL '1 day')
) AS o(email, store_name, method_name, status, total, address, age)
JOIN users u ON u.email = o.email
JOIN stores s ON s.name = o.store_name
JOIN delivery_methods d ON d.name = o.method_name
ON CONFLICT DO NOTHING;

-- Order line items, with the price snapshotted at order time.
INSERT INTO order_items (order_id, variant_id, quantity, unit_price)
SELECT o.order_id, v.variant_id, 1, v.price
FROM orders o
JOIN users u ON u.user_id = o.user_id
JOIN product_variants v ON v.variant_id = (
  SELECT MIN(variant_id) FROM product_variants WHERE product_id = (
    SELECT product_id FROM products WHERE name =
      CASE o.status
        WHEN 'Delivered' THEN 'Nimbus 25'
        WHEN 'Shipped'   THEN 'Glycerin 20'
        WHEN 'Confirmed' THEN 'Peregrine 13'
        ELSE 'Atmos AG 65'
      END
  )
)
WHERE NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.order_id);

-- Populate embeddings (AlloyDB / Cloud SQL only):
-- UPDATE products
--    SET embedding = embedding('text-embedding-005',
--                              name || ' ' || brand || ' ' || category || ' ' || description)::vector
--  WHERE embedding IS NULL;
