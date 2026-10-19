CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE stores (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL, slug text UNIQUE NOT NULL,
  website_url text, membership_required boolean NOT NULL DEFAULT false
);
CREATE TABLE store_locations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  store_id uuid NOT NULL REFERENCES stores(id),
  provider_location_id text, postal_code text, fsa char(3) NOT NULL,
  province char(2) NOT NULL, latitude numeric(9,6), longitude numeric(9,6),
  UNIQUE(store_id, provider_location_id)
);
CREATE TABLE items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_name text NOT NULL, brand text, exact_model text, gtin text,
  category text NOT NULL, variant jsonb NOT NULL DEFAULT '{}',
  base_quantity numeric(16,6) CHECK (base_quantity > 0),
  unit_basis text CHECK(unit_basis IN ('100g','100ml','roll','unit')),
  comparison_group text, embedding vector(768),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX items_embedding_idx ON items USING hnsw (embedding vector_cosine_ops);
CREATE TABLE rfd_posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_url text UNIQUE NOT NULL, title text NOT NULL, retailer_tag text,
  upvotes integer CHECK(upvotes >= 0), downvotes integer CHECK(downvotes >= 0),
  score integer, deal_url text, published_at timestamptz,
  observed_at timestamptz NOT NULL DEFAULT now(), raw jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE offers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id uuid REFERENCES items(id), store_id uuid NOT NULL REFERENCES stores(id),
  store_location_id uuid REFERENCES store_locations(id),
  source text NOT NULL CHECK(source IN ('flipp','reebee','retailer','rfd','shopping')),
  source_id text NOT NULL, source_url text NOT NULL,
  rfd_post_id uuid REFERENCES rfd_posts(id), fsa char(3),
  currency char(3) NOT NULL DEFAULT 'CAD' CHECK(currency = 'CAD'),
  sale_price numeric(12,4) NOT NULL CHECK(sale_price >= 0),
  regular_price numeric(12,4) CHECK(regular_price >= 0),
  reference_price_kind text CHECK(reference_price_kind IN ('msrp','retailer_regular','historical_median')),
  unit_price numeric(16,6) CHECK(unit_price >= 0), unit_basis text,
  conditions jsonb NOT NULL DEFAULT '{}', availability text NOT NULL DEFAULT 'unknown',
  valid_from timestamptz, valid_to timestamptz,
  observed_at timestamptz NOT NULL DEFAULT now(), verified_at timestamptz,
  match_confidence numeric(4,3) CHECK(match_confidence BETWEEN 0 AND 1),
  extraction_confidence numeric(4,3) CHECK(extraction_confidence BETWEEN 0 AND 1),
  raw jsonb NOT NULL DEFAULT '{}',
  CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
  UNIQUE(source, source_id, store_id, store_location_id, fsa)
);
-- PostgreSQL 15+: enforce uniqueness when location/FSA are NULL as well.
CREATE UNIQUE INDEX offers_source_identity ON offers
  (source, source_id, store_id, store_location_id, fsa) NULLS NOT DISTINCT;
CREATE INDEX offers_local_idx ON offers(fsa, item_id, valid_to);
CREATE TABLE flyer_deals (
  offer_id uuid PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE,
  flyer_id text NOT NULL, thumbnail_url text,
  polygon jsonb, clipping_url text, circular_timezone text NOT NULL,
  coverage_variant text
);
CREATE TABLE price_observations (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  offer_id uuid NOT NULL REFERENCES offers(id),
  item_id uuid NOT NULL REFERENCES items(id),
  store_location_id uuid REFERENCES store_locations(id), fsa char(3),
  landed_price numeric(12,4) NOT NULL CHECK(landed_price >= 0),
  unit_price numeric(16,6), conditions_hash text NOT NULL,
  observed_at timestamptz NOT NULL DEFAULT now(), source_url text NOT NULL
);
CREATE INDEX price_history_idx ON price_observations(item_id, fsa, observed_at DESC);
