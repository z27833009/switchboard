-- Schema for the public Olist CSVs. Loaded by `switchboard data load-olist`.
-- Types follow the source files: timestamps are naive local times and money is
-- numeric with two decimals. The loader truncates and reloads these tables.

CREATE SCHEMA IF NOT EXISTS olist;

CREATE TABLE IF NOT EXISTS olist.customers (
    customer_id text PRIMARY KEY,
    customer_unique_id text NOT NULL,
    customer_zip_code_prefix integer,
    customer_city text,
    customer_state text
);
CREATE INDEX IF NOT EXISTS customers_unique_idx ON olist.customers (customer_unique_id);

CREATE TABLE IF NOT EXISTS olist.geolocation (
    geolocation_zip_code_prefix integer NOT NULL,
    geolocation_lat double precision NOT NULL,
    geolocation_lng double precision NOT NULL,
    geolocation_city text,
    geolocation_state text
);
CREATE INDEX IF NOT EXISTS geolocation_zip_idx ON olist.geolocation (geolocation_zip_code_prefix);

CREATE TABLE IF NOT EXISTS olist.orders (
    order_id text PRIMARY KEY,
    customer_id text NOT NULL,
    order_status text NOT NULL,
    order_purchase_timestamp timestamp NOT NULL,
    order_approved_at timestamp,
    order_delivered_carrier_date timestamp,
    order_delivered_customer_date timestamp,
    order_estimated_delivery_date timestamp NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_purchase_idx ON olist.orders (order_purchase_timestamp);
CREATE INDEX IF NOT EXISTS orders_status_idx ON olist.orders (order_status);
CREATE INDEX IF NOT EXISTS orders_customer_idx ON olist.orders (customer_id);

CREATE TABLE IF NOT EXISTS olist.order_items (
    order_id text NOT NULL,
    order_item_id integer NOT NULL,
    product_id text NOT NULL,
    seller_id text NOT NULL,
    shipping_limit_date timestamp,
    price numeric(12, 2) NOT NULL,
    freight_value numeric(12, 2) NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);
CREATE INDEX IF NOT EXISTS order_items_product_idx ON olist.order_items (product_id);
CREATE INDEX IF NOT EXISTS order_items_seller_idx ON olist.order_items (seller_id);

CREATE TABLE IF NOT EXISTS olist.order_payments (
    order_id text NOT NULL,
    payment_sequential integer NOT NULL,
    payment_type text NOT NULL,
    payment_installments integer NOT NULL,
    payment_value numeric(12, 2) NOT NULL,
    PRIMARY KEY (order_id, payment_sequential)
);

CREATE TABLE IF NOT EXISTS olist.order_reviews (
    review_id text NOT NULL,
    order_id text NOT NULL,
    review_score smallint NOT NULL,
    review_comment_title text,
    review_comment_message text,
    review_creation_date timestamp,
    review_answer_timestamp timestamp
);
CREATE INDEX IF NOT EXISTS order_reviews_order_idx ON olist.order_reviews (order_id);
CREATE INDEX IF NOT EXISTS order_reviews_review_idx ON olist.order_reviews (review_id);

CREATE TABLE IF NOT EXISTS olist.products (
    product_id text PRIMARY KEY,
    product_category_name text,
    product_name_lenght integer,
    product_description_lenght integer,
    product_photos_qty integer,
    product_weight_g integer,
    product_length_cm integer,
    product_height_cm integer,
    product_width_cm integer
);
CREATE INDEX IF NOT EXISTS products_category_idx ON olist.products (product_category_name);

CREATE TABLE IF NOT EXISTS olist.sellers (
    seller_id text PRIMARY KEY,
    seller_zip_code_prefix integer,
    seller_city text,
    seller_state text
);

CREATE TABLE IF NOT EXISTS olist.product_category_name_translation (
    product_category_name text PRIMARY KEY,
    product_category_name_english text NOT NULL
);
