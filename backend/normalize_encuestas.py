import psycopg2

def get_conn():
    return psycopg2.connect(
        host="localhost",
        port="5432",
        database="Rh-System",
        user="postgres",
        password="S1s73m4s!",
    )

case_sql = """
UPDATE encuesta_reclutamiento
SET fuente = CASE
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%FACEBOOK%' THEN 'FACEBOOK'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%POSTEO%' THEN 'POSTEO'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%VOLANTE%' THEN 'VOLANTE'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%RECOM%' THEN 'RECOMENDACION'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%AGENCIA%' AND (UPPER(COALESCE(fuente, '')) LIKE '%CW%' OR UPPER(COALESCE(fuente, '')) LIKE '% C W%') THEN 'RECLU CW'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%AGENCIA%' AND UPPER(COALESCE(fuente, '')) LIKE '%PURO%' THEN 'RECLU PURO'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%CW%' THEN 'RECLU CW'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%PURO%' THEN 'RECLU PURO'
  ELSE UPPER(TRIM(COALESCE(fuente, 'OTRO')))
END
WHERE fuente IS NOT NULL;
"""

summary_sql = "SELECT fuente, COUNT(*) FROM encuesta_reclutamiento GROUP BY fuente ORDER BY COUNT(*) DESC;"

if __name__ == '__main__':
    with get_conn() as conn:
        with conn.cursor() as cur:
            print('Before:')
            cur.execute(summary_sql)
            for row in cur.fetchall():
                print(row)

            cur.execute(case_sql)
            conn.commit()

            print('\nAfter:')
            cur.execute(summary_sql)
            for row in cur.fetchall():
                print(row)

    print('\nDone')
