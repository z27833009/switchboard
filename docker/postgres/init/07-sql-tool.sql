-- Read-only SQL tool (T3.05), design.md sections 7.1 and 11.
--
-- The `query_readonly_sql@1` tool runs a model-written SELECT, so the model gets
-- no direct access to the Olist tables. Everything it may read is flattened into
-- one documented fact view, and the tenant filter lives inside that view: the
-- tool server sets `switchboard.seller_ids` on the transaction (a comma
-- separated list, empty for every seller) and the view reads it.
--
-- `security_barrier` is what keeps the filter honest: PostgreSQL may not push a
-- caller predicate below the view, so a predicate on `seller_id` from the model
-- cannot change which rows the view itself admits.
--
-- The view references the tables the Olist loader creates, and a fresh volume
-- runs this file before any data exists, so the whole migration is skipped with
-- a notice when those tables are missing. The loader re-applies this file after
-- loading (and a data reload therefore keeps the view), and an existing volume
-- with data can run it by hand:
--
--   docker compose exec -T postgres psql -U switchboard -d switchboard \
--     -f /docker-entrypoint-initdb.d/07-sql-tool.sql
--
-- Every statement is idempotent, so re-applying is safe.

DO $$
BEGIN
    IF to_regclass('olist.order_items') IS NULL
       OR to_regclass('olist.orders') IS NULL
       OR to_regclass('olist.products') IS NULL
       OR to_regclass('olist.product_category_name_translation') IS NULL THEN
        RAISE NOTICE 'olist tables are not loaded yet; skipping 07-sql-tool.sql';
        RETURN;
    END IF;

    CREATE OR REPLACE VIEW olist.sql_order_facts
    WITH (security_barrier = true) AS
    SELECT
        i.order_id,
        i.order_item_id,
        i.seller_id,
        i.product_id,
        coalesce(
            t.product_category_name_english,
            p.product_category_name,
            'unknown'
        ) AS category,
        i.price,
        i.freight_value,
        o.order_status,
        o.order_purchase_timestamp::date AS purchase_date,
        o.order_delivered_customer_date::date AS delivered_date,
        o.order_estimated_delivery_date::date AS estimated_date,
        -- Positive means the delivery arrived after the estimate; null means
        -- the order has no delivery date yet. The same arithmetic the fixed
        -- tools use (design.md section 7.1).
        (
            o.order_delivered_customer_date::date
            - o.order_estimated_delivery_date::date
        ) AS days_late
    FROM olist.order_items i
    JOIN olist.orders o ON o.order_id = i.order_id
    LEFT JOIN olist.products p ON p.product_id = i.product_id
    LEFT JOIN olist.product_category_name_translation t
           ON t.product_category_name = p.product_category_name
    WHERE coalesce(current_setting('switchboard.seller_ids', true), '') = ''
       OR i.seller_id = ANY (
            string_to_array(current_setting('switchboard.seller_ids', true), ',')
       );

    COMMENT ON VIEW olist.sql_order_facts IS
        'One row per order item, the only relation query_readonly_sql@1 may read. '
        'Rows are filtered by switchboard.seller_ids (comma separated, empty means '
        'every seller), set by the tool server per transaction.';

    -- A view built for one tool is not a general-purpose table: the API role
    -- gets nothing and the tool role gets SELECT only. The view owner is the
    -- bootstrap user, so the tool role needs no privilege on the base tables to
    -- read it.
    REVOKE ALL ON olist.sql_order_facts FROM PUBLIC;
    REVOKE ALL ON olist.sql_order_facts FROM switchboard_app;
    GRANT SELECT ON olist.sql_order_facts TO switchboard_tools;
END
$$;
