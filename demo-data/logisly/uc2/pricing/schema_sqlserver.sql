-- Schema of the demo pricing DB, written for SQL Server (Logisly's pricing
-- store per the 29 Oct MOM). The demo ships the same tables in SQLite
-- (pricing.db); a real adapter maps Logisly's actual tables onto these.
CREATE TABLE customers (
    customer_id        VARCHAR(12)   NOT NULL PRIMARY KEY,
    name               NVARCHAR(200) NOT NULL,
    segment            NVARCHAR(60)  NOT NULL,
    city               NVARCHAR(80)  NOT NULL,
    customer_since     DATE          NOT NULL,
    payment_term_days  INT           NOT NULL,
    status             VARCHAR(10)   NOT NULL
);
CREATE TABLE transporters (
    transporter_id  VARCHAR(10)   NOT NULL PRIMARY KEY,
    name            NVARCHAR(200) NOT NULL,
    city            NVARCHAR(80)  NOT NULL,
    email           VARCHAR(200)  NOT NULL,
    contact_person  NVARCHAR(120) NOT NULL,
    regions         VARCHAR(100)  NOT NULL
);
CREATE TABLE truck_types (
    truck_type    VARCHAR(12)   NOT NULL PRIMARY KEY,
    name          NVARCHAR(60)  NOT NULL,
    capacity_ton  DECIMAL(5,1)  NOT NULL
);
CREATE TABLE lanes (
    lane_id      VARCHAR(8)    NOT NULL PRIMARY KEY,
    origin       NVARCHAR(80)  NOT NULL,
    destination  NVARCHAR(80)  NOT NULL,
    distance_km  INT           NOT NULL,
    ferry        BIT           NOT NULL
);
-- Agreed buy rates from transporters, one row per transporter, lane, truck and month.
CREATE TABLE rate_history (
    rate_id         INT          NOT NULL PRIMARY KEY,
    transporter_id  VARCHAR(10)  NOT NULL REFERENCES transporters(transporter_id),
    lane_id         VARCHAR(8)   NOT NULL REFERENCES lanes(lane_id),
    truck_type      VARCHAR(12)  NOT NULL REFERENCES truck_types(truck_type),
    period          CHAR(7)      NOT NULL,  -- YYYY-MM
    rate_idr        BIGINT       NOT NULL,  -- per trip, incl. toll and ferry
    trips           INT          NOT NULL,
    source          VARCHAR(12)  NOT NULL   -- PO | quotation
);
-- Sell rates agreed with shippers.
CREATE TABLE customer_contract_rates (
    contract_rate_id  INT          NOT NULL PRIMARY KEY,
    customer_id       VARCHAR(12)  NOT NULL REFERENCES customers(customer_id),
    lane_id           VARCHAR(8)   NOT NULL REFERENCES lanes(lane_id),
    truck_type        VARCHAR(12)  NOT NULL REFERENCES truck_types(truck_type),
    sell_rate_idr     BIGINT       NOT NULL,
    valid_from        DATE         NOT NULL,
    valid_to          DATE         NOT NULL
);
CREATE TABLE transporter_performance (
    transporter_id  VARCHAR(10)   NOT NULL REFERENCES transporters(transporter_id),
    quarter         CHAR(7)       NOT NULL,  -- YYYY-Qn
    trips           INT           NOT NULL,
    on_time_pct     DECIMAL(4,1)  NOT NULL,
    claim_pct       DECIMAL(4,2)  NOT NULL,
    acceptance_pct  DECIMAL(4,1)  NOT NULL,
    PRIMARY KEY (transporter_id, quarter)
);
