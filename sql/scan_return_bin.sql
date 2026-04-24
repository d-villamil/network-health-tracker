-- Scan Return Bin counts per site (RETURNED_TO_BIN state, today, is_latest=true)
WITH pod_map AS (
    SELECT * FROM (VALUES
        ('BKN-9', 'POD 1'), ('HUD-1', 'POD 1'), ('PHL-7', 'POD 1'), ('ORL-3', 'POD 1'),
        ('BLT-3', 'POD 1'), ('ATL-11', 'POD 1'), ('PHL-8', 'POD 1'), ('DAL-8', 'POD 1'),
        ('LIN-1', 'POD 1'), ('RAL-3', 'POD 1'),
        ('CHI-17', 'POD 2'), ('HBG-2', 'POD 2'), ('PIT-2', 'POD 2'), ('HGR-1', 'POD 2'),
        ('ATX-5', 'POD 2'), ('MSP-6', 'POD 2'), ('CNJ-2', 'POD 2'), ('RIC-2', 'POD 2'),
        ('VAB-4', 'POD 2'), ('BOS-5', 'POD 2'), ('ATL-12', 'POD 2'),
        ('HOU-10', 'POD 3'), ('TPA-4', 'POD 3'), ('CLT-3', 'POD 3'), ('OCO-1', 'POD 3'),
        ('NNJ-6', 'POD 3'), ('HFD-3', 'POD 3'), ('JAX-2', 'POD 3'), ('QNS-2', 'POD 3'),
        ('DET-13', 'POD 3'), ('BNX-1', 'POD 3'),
        ('COL-5', 'POD 4'), ('DAL-9', 'POD 4'), ('DCA-5', 'POD 4'), ('CIN-5', 'POD 4'),
        ('NNJ-5', 'POD 4'), ('BOS-6', 'POD 4'), ('NAS-2', 'POD 4'), ('CLE-7', 'POD 4'),
        ('INE-11', 'POD 4'), ('SAT-4', 'POD 4'),
        ('LAX-8', 'POD 5'), ('MSP-5', 'POD 5'), ('SND-3', 'POD 5'), ('LAV-5', 'POD 5'),
        ('MSP-4', 'POD 5'), ('EBY-3', 'POD 5'), ('LAS-3', 'POD 5'), ('CLV-1', 'POD 5'),
        ('PHX-8', 'POD 5'), ('SAC-4', 'POD 5')
    ) AS t (facility_code, pod)
)
SELECT
    pm.facility_code AS site,
    pm.pod,
    COUNT(DISTINCT CASE WHEN upper(p.parcel_state) = 'RETURNED_TO_BIN' THEN p.barcode END) AS scan_return_bin
FROM
    pod_map pm
    LEFT JOIN datalake.parcel_service_prod.parcel_state_event p
        ON p.facility_code = pm.facility_code
        AND p.is_latest = true
        AND DATE(p.event_time) = CURRENT_DATE
GROUP BY
    pm.facility_code, pm.pod
ORDER BY
    scan_return_bin DESC
