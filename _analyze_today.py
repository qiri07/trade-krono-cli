import sqlite3

con = sqlite3.connect('outputs/cache/pipeline_cache.db')
cur = con.cursor()

# 今日记录按ticker前缀分类
print('=== 今日(2026-11-16)记录按ticker前缀分类 ===')
for row in cur.execute("""
    SELECT
        CASE
            WHEN ticker LIKE 'sh.%' THEN 'sh (沪市)'
            WHEN ticker LIKE 'sz.%' THEN 'sz (深市)'
            WHEN ticker LIKE 'bj.%' THEN 'bj (北交所)'
            ELSE 'other'
        END as market,
        COUNT(*) as cnt
    FROM kline_cache
    WHERE end = '2026-11-16'
    GROUP BY market
"""):
    print(f'  {row[0]}: {row[1]} 条')

# 今日ETF记录
print('\n=== 今日ETF记录 (sh.5x / sz.159x / sz.156x / sz.158x) ===')
etf_cnt = cur.execute("""
    SELECT COUNT(*) FROM kline_cache
    WHERE end = '2026-11-16'
    AND (
        ticker LIKE 'sh.5%' OR
        ticker LIKE 'sz.159%' OR
        ticker LIKE 'sz.156%' OR
        ticker LIKE 'sz.158%'
    )
""").fetchone()[0]
print(f'  ETF记录数: {etf_cnt} 条')

# 今日A股记录（非ETF）
print('\n=== 今日A股记录 (非ETF) ===')
a_cnt = cur.execute("""
    SELECT COUNT(*) FROM kline_cache
    WHERE end = '2026-11-16'
    AND NOT (
        ticker LIKE 'sh.5%' OR
        ticker LIKE 'sz.159%' OR
        ticker LIKE 'sz.156%' OR
        ticker LIKE 'sz.158%'
    )
""").fetchone()[0]
print(f'  A股记录数: {a_cnt} 条')

# 显示今日具体ticker列表（前30个）
print('\n=== 今日记录的ticker样本 (前30个) ===')
for row in cur.execute("""
    SELECT ticker, COUNT(*) as cnt
    FROM kline_cache
    WHERE end = '2026-11-16'
    GROUP BY ticker
    ORDER BY ticker
    LIMIT 30
"""):
    print(f'  {row[0]}: {row[1]} 条')

con.close()
