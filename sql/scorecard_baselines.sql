WITH warehouse_tz AS (
  SELECT
    CAST(w.id AS varchar) AS facility_code,
    json_extract_scalar(w.address, '$.timezone') AS timezone
  FROM datalake.parcel_service_prod.warehouse w
),
pod_map AS (
  SELECT * FROM (
    VALUES
      -- POD 1
      ('BKN-9','POD 1'),('HUD-1','POD 1'),('PHL-7','POD 1'),('ORL-3','POD 1'),('BLT-3','POD 1'),
      ('ATL-11','POD 1'),('PHL-8','POD 1'),('DAL-8','POD 1'),('LIN-1','POD 1'),('RAL-3','POD 1'),('ATL-12','POD 1'),

      -- POD 2
      ('CHI-17','POD 2'),('HBG-2','POD 2'),('PIT-2','POD 2'),('HGR-1','POD 2'),('ATX-5','POD 2'),('MSP-6','POD 2'),
      ('CNJ-2','POD 2'),('RIC-2','POD 2'),('VAB-4','POD 2'),('BOS-5','POD 2'),

      -- POD 3
      ('HOU-10','POD 3'),('TPA-4','POD 3'),('CLT-3','POD 3'),('OCO-1','POD 3'),('NNJ-6','POD 3'),
      ('HFD-3','POD 3'),('JAX-2','POD 3'),('QNS-2','POD 3'),('DET-13','POD 3'),('BNX-1','POD 3'),

      -- POD 4
      ('COL-5','POD 4'),('DAL-9','POD 4'),('DCA-5','POD 4'),('CIN-5','POD 4'),('NNJ-5','POD 4'),
      ('BOS-6','POD 4'),('NAS-2','POD 4'),('CLE-7','POD 4'),('INE-11','POD 4'),('SAT-4','POD 4'),

      -- POD 5
      ('LAX-9','POD 5'),('MSP-5','POD 5'),('SND-3','POD 5'),('LAV-5','POD 5'),('MSP-4','POD 5'),
      ('EBY-3','POD 5'),('LAS-3','POD 5'),('CLV-1','POD 5'),('SAC-5','POD 5'),('PHX-8','POD 5'),
      ('SAC-4','POD 5'),('LAX-8','POD 5')
  ) AS t(facility_code, pod)
),

stow_events_local AS (
  SELECT
    pse.facility_code,
    wt.timezone,
    CAST(at_timezone(pse.event_time, wt.timezone) AS timestamp) AS event_time_local,
    DATE(CAST(at_timezone(pse.event_time, wt.timezone) AS timestamp)) AS local_day,
    pse.barcode
  FROM datalake.parcel_service_prod.parcel_state_event pse
  JOIN warehouse_tz wt
    ON CAST(pse.facility_code AS varchar) = wt.facility_code
  WHERE pse.facility_code NOT IN ('SND-1', 'TEST')
    AND DATE(pse.event_time) BETWEEN CURRENT_DATE - INTERVAL '30' DAY AND CURRENT_DATE
    AND upper(pse.parcel_state) = 'READY_TO_STOW'
),

stow_first_per_barcode_day AS (
  SELECT
    facility_code,
    timezone,
    local_day,
    barcode,
    MIN(event_time_local) AS stow_ts_local
  FROM stow_events_local
  GROUP BY facility_code, timezone, local_day, barcode
),

daily_totals AS (
  SELECT
    facility_code,
    timezone,
    local_day,
    COUNT(DISTINCT barcode) AS total_barcodes_day
  FROM stow_first_per_barcode_day
  GROUP BY facility_code, timezone, local_day
),

window_counts AS (
  SELECT
    a.facility_code,
    a.timezone,
    a.local_day,
    a.stow_ts_local AS window_start_ts,
    COUNT(*) AS barcodes_in_5m
  FROM stow_first_per_barcode_day a
  JOIN stow_first_per_barcode_day b
    ON a.facility_code = b.facility_code
   AND a.local_day = b.local_day
   AND b.stow_ts_local BETWEEN a.stow_ts_local AND a.stow_ts_local + INTERVAL '5' MINUTE
  GROUP BY a.facility_code, a.timezone, a.local_day, a.stow_ts_local
),

first_last_windows AS (
  SELECT
    facility_code,
    timezone,
    local_day,
    MIN(window_start_ts) AS start_clock_local,
    MAX(window_start_ts) AS stop_clock_window_start_local
  FROM window_counts
  WHERE barcodes_in_5m >= 25
  GROUP BY facility_code, timezone, local_day
),

daily_throughput AS (
  SELECT
    d.facility_code,
    d.timezone,
    d.local_day,
    d.total_barcodes_day,
    w.start_clock_local,
    (w.stop_clock_window_start_local + INTERVAL '5' MINUTE) AS stop_clock_local,
    CASE
      WHEN w.start_clock_local IS NULL OR w.stop_clock_window_start_local IS NULL THEN NULL
      ELSE date_diff('minute', w.start_clock_local, w.stop_clock_window_start_local + INTERVAL '5' MINUTE)
    END AS scanning_minutes,
    CASE
      WHEN w.start_clock_local IS NULL OR w.stop_clock_window_start_local IS NULL THEN NULL
      WHEN date_diff('minute', w.start_clock_local, w.stop_clock_window_start_local + INTERVAL '5' MINUTE) <= 0 THEN NULL
      ELSE CAST(d.total_barcodes_day AS DOUBLE) /
           date_diff('minute', w.start_clock_local, w.stop_clock_window_start_local + INTERVAL '5' MINUTE)
    END AS throughput_barcodes_per_min
  FROM daily_totals d
  LEFT JOIN first_last_windows w
    ON d.facility_code = w.facility_code
   AND d.local_day = w.local_day
),

lfr_events_local AS (
  SELECT
    pse.facility_code,
    wt.timezone,
    pse.barcode,
    CAST(at_timezone(pse.event_time, wt.timezone) AS timestamp) AS event_time_local,
    DATE(CAST(at_timezone(pse.event_time, wt.timezone) AS timestamp)) AS local_day
  FROM datalake.parcel_service_prod.parcel_state_event pse
  JOIN warehouse_tz wt
    ON CAST(pse.facility_code AS varchar) = wt.facility_code
  WHERE pse.facility_code NOT IN ('SND-1', 'TEST')
    AND DATE(pse.event_time) BETWEEN CURRENT_DATE - INTERVAL '30' DAY AND CURRENT_DATE
    AND upper(pse.parcel_state) = 'LOOKING_FOR_RUNNERS'
),

lfr_first_per_barcode_day AS (
  SELECT
    facility_code,
    timezone,
    local_day,
    barcode,
    MIN(event_time_local) AS first_lfr_local_ts
  FROM lfr_events_local
  GROUP BY facility_code, timezone, local_day, barcode
),

lfr_ranked AS (
  SELECT
    facility_code,
    timezone,
    local_day,
    first_lfr_local_ts,
    ROW_NUMBER() OVER (
      PARTITION BY facility_code, local_day
      ORDER BY first_lfr_local_ts ASC
    ) AS rn
  FROM lfr_first_per_barcode_day
),

avg_dispatch_start AS (
  SELECT
    facility_code,
    timezone,
    format_datetime(
      date_add(
        'second',
        CAST(AVG(date_diff('second', date_trunc('day', first_lfr_local_ts), first_lfr_local_ts)) AS bigint),
        TIMESTAMP '1970-01-01 00:00:00'
      ),
      'HH:mm:ss'
    ) AS avg_dispatch_start_time_local
  FROM lfr_ranked
  WHERE rn = 10
    AND local_day BETWEEN CURRENT_DATE - INTERVAL '30' DAY AND CURRENT_DATE - INTERVAL '1' DAY
  GROUP BY facility_code, timezone
),

today_dispatch_start AS (
  SELECT
    facility_code,
    timezone,
    format_datetime(
      date_add(
        'second',
        CAST(date_diff('second', date_trunc('day', first_lfr_local_ts), first_lfr_local_ts) AS bigint),
        TIMESTAMP '1970-01-01 00:00:00'
      ),
      'HH:mm:ss'
    ) AS today_dispatch_start_time_local
  FROM lfr_ranked
  WHERE rn = 10
    AND local_day = CURRENT_DATE
),

facility_avgs AS (
  SELECT
    facility_code,
    timezone,
    ROUND(AVG(total_barcodes_day), 1) AS avg_total_barcodes_day,
    CAST(AVG(date_diff('second', date_trunc('day', start_clock_local), start_clock_local)) AS bigint)
      AS avg_start_clock_seconds,
    CAST(AVG(date_diff('second', date_trunc('day', stop_clock_local), stop_clock_local)) AS bigint)
      AS avg_stop_clock_seconds,
    ROUND(AVG(scanning_minutes), 1) AS avg_scanning_minutes,
    ROUND(AVG(throughput_barcodes_per_min), 3) AS avg_throughput_barcodes_per_min
  FROM daily_throughput
  WHERE local_day BETWEEN CURRENT_DATE - INTERVAL '30' DAY AND CURRENT_DATE - INTERVAL '1' DAY
  GROUP BY facility_code, timezone
),

today_throughput AS (
  SELECT
    facility_code,
    timezone,
    total_barcodes_day AS today_total_barcodes,
    start_clock_local  AS today_start_clock_local,
    scanning_minutes   AS today_scanning_minutes,
    throughput_barcodes_per_min AS today_throughput_barcodes_per_min
  FROM daily_throughput
  WHERE local_day = CURRENT_DATE
),

batch_sizes_by_day AS (
  SELECT
    p.hub_external_store_id AS facility_code,
    DATE(CAST(at_timezone(p.quoted_pickup_time, wt.timezone) AS timestamp)) AS local_day,
    p.force_batch_id,
    COUNT(DISTINCT p.barcode) AS barcodes_in_batch
  FROM datalake.parcel_service_prod.parcels p
  JOIN warehouse_tz wt
    ON CAST(p.hub_external_store_id AS varchar) = wt.facility_code
  WHERE p.force_batch_id IS NOT NULL
    AND p.hub_external_store_id NOT IN ('SND-1', 'TEST')
    AND DATE(CAST(at_timezone(p.quoted_pickup_time, wt.timezone) AS timestamp))
        BETWEEN CURRENT_DATE - INTERVAL '30' DAY AND CURRENT_DATE
    AND Is_Test <> True
  GROUP BY
    p.hub_external_store_id,
    DATE(CAST(at_timezone(p.quoted_pickup_time, wt.timezone) AS timestamp)),
    p.force_batch_id
),

batch_daily AS (
  SELECT
    facility_code,
    local_day,
    COUNT(*) AS total_batches,
    SUM(CASE WHEN barcodes_in_batch < 15 THEN 1 ELSE 0 END) AS batches_under_15
  FROM batch_sizes_by_day
  GROUP BY facility_code, local_day
),

batch_avgs AS (
  SELECT
    bd.facility_code,
    ROUND(AVG(bd.total_batches), 1) AS avg_total_batches_per_day,
    ROUND(AVG(bd.batches_under_15), 1) AS avg_batches_under_15_per_day
  FROM batch_daily bd
  WHERE bd.local_day BETWEEN CURRENT_DATE - INTERVAL '30' DAY AND CURRENT_DATE - INTERVAL '1' DAY
  GROUP BY bd.facility_code
),

batch_today AS (
  SELECT
    facility_code,
    total_batches AS today_total_batches,
    batches_under_15 AS today_batches_under_15
  FROM batch_daily
  WHERE local_day = CURRENT_DATE
)

SELECT
  fa.facility_code,
  COALESCE(pm.pod, 'Other') AS pod,
  CASE
    WHEN fa.timezone = 'America/New_York' THEN '1. Eastern'
    WHEN fa.timezone = 'America/Chicago' THEN '2. Central'
    WHEN fa.timezone = 'America/Los_Angeles' THEN '3. Pacific'
    ELSE fa.timezone
  END AS timezone,
  fa.avg_total_barcodes_day,
  date_add(
    'second',
    fa.avg_start_clock_seconds,
    date_trunc('day', CAST(at_timezone(current_timestamp, fa.timezone) AS timestamp))
  ) AS avg_start_clock_local_ts,
  date_add(
    'second',
    fa.avg_stop_clock_seconds,
    date_trunc('day', CAST(at_timezone(current_timestamp, fa.timezone) AS timestamp))
  ) AS avg_stop_clock_local_ts,
  fa.avg_scanning_minutes,
  fa.avg_throughput_barcodes_per_min,
  ads.avg_dispatch_start_time_local,
  tt.today_total_barcodes,
  tt.today_start_clock_local AS today_start_clock_local_ts,
  tt.today_scanning_minutes,
  tt.today_throughput_barcodes_per_min,
  td.today_dispatch_start_time_local,
  bt.today_total_batches,
  bt.today_batches_under_15
FROM facility_avgs fa
LEFT JOIN avg_dispatch_start ads
  ON fa.facility_code = ads.facility_code AND fa.timezone = ads.timezone
LEFT JOIN today_throughput tt
  ON fa.facility_code = tt.facility_code AND fa.timezone = tt.timezone
LEFT JOIN today_dispatch_start td
  ON fa.facility_code = td.facility_code AND fa.timezone = td.timezone
LEFT JOIN batch_avgs ba
  ON fa.facility_code = ba.facility_code
LEFT JOIN batch_today bt
  ON fa.facility_code = bt.facility_code
LEFT JOIN pod_map pm
  ON fa.facility_code = pm.facility_code
ORDER BY timezone, fa.facility_code
