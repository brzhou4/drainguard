-- DrainGuard tables for Supabase (free tier is plenty). Paste into Supabase > SQL Editor > Run.
-- Then set SUPABASE_URL and SUPABASE_KEY in Streamlit secrets. Use the service_role key: it only ever
-- lives on the Streamlit server, and with RLS on and no policies, nobody else can read the tables.
-- Every column is text so the app can treat Supabase and local CSVs the same way.

create table if not exists volunteers (
  id bigserial primary key,
  token text not null, topic text, lat text, lon text, radius_m text,
  lang text, adult text, created text   -- anonymous: no names
);
create index if not exists volunteers_token on volunteers (token);

create table if not exists drain_status (
  id bigserial primary key,
  asset_id text not null, status text, volunteer text, volunteer_token text, time text,
  condition text, note text, photo text
);
create index if not exists drain_status_asset on drain_status (asset_id);

create table if not exists claims (
  id bigserial primary key,
  asset_id text not null, token text, until text   -- volunteer en route until this time
);

alter table volunteers enable row level security;
alter table drain_status enable row level security;
alter table claims enable row level security;

-- Photos: create a PRIVATE storage bucket named "photos" (Storage > New bucket, Public off).
