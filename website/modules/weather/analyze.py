#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_weather.py
分析 weather/number.txt 数据并生成：
- 插值后小时/每日用量
- RNN 暖气预测（predicted_heat_simple.csv）
- 7天均值外推预测（forecast_usage.csv）
- usage_forecast.svg + 其他图表
"""

import os, sys, requests
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from scipy.interpolate import PchipInterpolator  # ✅ 引入新工具
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.weather.db import active_period
from modules.weather.history import load_historical_weather

# ----------------------------- 基础设置 -----------------------------
DATA_DIR = os.environ.get("WEATHER_DATA_DIR", "/home/bbdwz/projects/website/data/weather")
OUTPUT_DIR = os.environ.get("WEATHER_OUTPUT_DIR", DATA_DIR)
TXT_FILE = os.path.join(DATA_DIR, "number.txt")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

matplotlib.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei']
matplotlib.rcParams['axes.unicode_minus'] = False
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams['figure.dpi'] = 150


def ensure_chinese_font():
    font_path = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
    if os.path.exists(font_path):
        import matplotlib.font_manager as fm
        font_prop = fm.FontProperties(fname=font_path)
        plt.rcParams["font.sans-serif"] = [font_prop.get_name()]
        plt.rcParams["axes.unicode_minus"] = False
        print(f"✅ 使用字体: {font_prop.get_name()}")
    else:
        print("⚠️ 未找到中文字体，将使用默认字体（可能乱码）")


ensure_chinese_font()


def format_time_axis(ax, by="hour"):
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter('%m-%d\n%H:%M' if by == "hour" else '%m-%d')
    )
    for label in ax.get_xticklabels():
        label.set_rotation(0)
        label.set_fontsize(8)


# ----------------------------- 1️⃣ 读取原始数据 -----------------------------
if not os.path.exists(TXT_FILE):
    print(f"❌ 未找到文件: {TXT_FILE}")
    sys.exit(0)

records = []
with open(TXT_FILE, 'r', encoding='utf-8') as f:
    lines = [line.strip() for line in f if line.strip()]
for i in range(0, len(lines), 2):
    try:
        t = datetime.strptime(lines[i], "%Y年%m月%d日 %H:%M")
        v = float(lines[i + 1])
        records.append((t, v))
    except:
        continue

if not records:
    print("❌ 无有效数据")
    sys.exit(0)

df = pd.DataFrame(records, columns=['ts', 'cum']).sort_values('ts').reset_index(drop=True)

# 管理员可选择非自然年周期；不设置时保持原有全量行为。
period_start = os.environ.get("WEATHER_PERIOD_START", "").strip()
if period_start:
    try:
        period_start_dt = pd.Timestamp(period_start)
        df = df[df['ts'] >= period_start_dt].reset_index(drop=True)
    except ValueError:
        print(f"❌ 无效周期开始日期: {period_start}")
        sys.exit(2)
    if len(df) < 2:
        print(f"❌ 周期 {period_start} 内至少需要两条读数")
        sys.exit(2)

# ----------------------------- 2️⃣ 插值生成小时数据 (修正版) -----------------------------
start_hour = df['ts'].iloc[0].replace(minute=0, second=0, microsecond=0)
end_hour = df['ts'].iloc[-1].replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
hour_edges = pd.date_range(start=start_hour, end=end_hour, freq='h')

x = df['ts'].astype(np.int64) / 1e9
y = df['cum'].to_numpy()

# ✅ 使用 PchipInterpolator
# PCHIP (Piecewise Cubic Hermite Interpolating Polynomial)
# 特性：保形插值。如果数据是单调的，插值结果也是单调的。绝不会出现过冲。
f = PchipInterpolator(x, y)

xe = hour_edges.astype(np.int64) / 1e9
c_edge = f(xe)

interp_df = pd.DataFrame({'datetime': hour_edges, 'cumulative_total': c_edge}).set_index('datetime')
interp_df['hourly_usage'] = interp_df['cumulative_total'].diff()

# 修正第一个点的 NaN (可选)
interp_df.loc[interp_df.index[0], 'hourly_usage'] = 0

hourly = interp_df.iloc[1:].copy()
hourly.to_csv(os.path.join(DATA_DIR, "hourly_usage.csv"), float_format='%.3f', encoding='utf-8-sig')

# ----------------------------- 3️⃣ 计算每日用量 -----------------------------
daily = pd.DataFrame()
daily['cumulative_total'] = interp_df['cumulative_total'].resample('D').last()
daily['daily_usage'] = interp_df['hourly_usage'].resample('D').sum()
daily.to_csv(os.path.join(DATA_DIR, "daily_usage.csv"), float_format='%.3f', encoding='utf-8-sig')

# ----------------------------- 4️⃣ 获取并缓存当前周期地点的历史气温 -----------------------------
period = active_period()
print(f"🌡️ 正在读取 {period['location_name']} 历史气温（{period['latitude']}, {period['longitude']}）...")
try:
    weather_df, weather_daily, fetched = load_historical_weather(
        period, df['ts'].iloc[0], df['ts'].iloc[-1]
    )
    print(
        f"✅ 历史天气：缓存共 {len(weather_df)} 小时 / {len(weather_daily)} 天；"
        f"本次新增 {fetched['hourly']} 小时 / {fetched['daily']} 天，"
        f"Archive {fetched['archive_ranges']} 段，Forecast {fetched['forecast_ranges']} 段"
    )
except Exception as e:
    print(f"⚠️ 无法读取或补全历史气温：{e}")
    weather_df, weather_daily = pd.DataFrame(), pd.DataFrame()

# ----------------------------- 未来真实 14 天气温（future_temp_api） -----------------------------
print("🌡️ 正在获取未来 14 天气温...")
try:
    url_future = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={period['latitude']}&longitude={period['longitude']}"
        "&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean"
        "&forecast_days=14"
        f"&timezone={period['timezone']}"
    )
    rf = requests.get(url_future, timeout=15)
    wf = rf.json()

    future_temp_api = pd.DataFrame({
        "date": pd.to_datetime(wf["daily"]["time"]),
        "tavg": wf["daily"]["temperature_2m_mean"],
        "tmin": wf["daily"]["temperature_2m_min"],
        "tmax": wf["daily"]["temperature_2m_max"],
    }).set_index("date")

    print(f"✅ future_temp_api 载入成功：{len(future_temp_api)} 天")

except Exception as e:
    print(f"⚠️ 未来气温下载失败：{e}")
    future_temp_api = pd.DataFrame()


# ======================================================================
# 5️⃣（已移除 RNN）这里不再进行 AI 预测
# ======================================================================
print("🤖 跳过 RNN 暖气预测（已移除模型部分）")
print("📄 不生成 predicted_heat_simple.csv")


# ======================================================================
# 6️⃣ usage_forecast.svg + forecast_usage.csv
# ======================================================================
print("📈 生成累计预测图 usage_forecast.svg ...")

def calc_cost(u):
    return 0.225988 * (u - 160) + 90

# 预测终点
future_end = datetime(
    df['ts'].iloc[-1].year + (1 if df['ts'].iloc[-1].month > 3 else 0),
    3, 31, 23, 0
)

# 使用 7 日平均外推未来用量
future_hours = pd.date_range(df['ts'].iloc[-1], future_end, freq='h')
recent_rate = interp_df['hourly_usage'].iloc[-24 * 7:].mean()
future_usage = interp_df['cumulative_total'].iloc[-1] + np.cumsum(
    np.full(len(future_hours), recent_rate)
)

forecast_df = pd.DataFrame({
    'datetime': future_hours,
    'forecast_cumulative': future_usage
}).set_index('datetime')

# ----------------------------- 保存 forecast_usage.csv -----------------------------
forecast_rows = []
key_dates = [
    datetime(df['ts'].iloc[-1].year, 12, 31, 23, 0),
    datetime(df['ts'].iloc[-1].year + 1, 3, 31, 23, 0)
]

for d in key_dates:
    if d not in forecast_df.index:
        nearest_idx = forecast_df.index.get_indexer([d], method='nearest')[0]
        d_actual = forecast_df.index[nearest_idx]
    else:
        d_actual = d

    u = forecast_df.loc[d_actual, 'forecast_cumulative']
    c = calc_cost(u)
    forecast_rows.append({
        "date": d_actual.strftime("%Y-%m-%d %H:%M"),
        "predicted_usage": round(u, 3),
        "predicted_cost": round(c, 2)
    })

forecast_csv = pd.DataFrame(forecast_rows)
forecast_csv_path = os.path.join(DATA_DIR, "forecast_usage.csv")
forecast_csv.to_csv(forecast_csv_path, index=False, encoding="utf-8-sig", float_format="%.3f")

print(f"✅ 已保存 forecast_usage.csv → {forecast_csv_path}")


# ======================================================================
# 绘制累计预测图
# ======================================================================
fig, ax1 = plt.subplots(figsize=(8, 4))

ax1.plot(interp_df.index, interp_df['cumulative_total'], '-',
         color='tab:blue', label='Historical (Interpolated)')

ax1.plot(forecast_df.index, forecast_df['forecast_cumulative'], '--',
         color='tab:orange', label='Forecast (Avg 7d)')

# RNN 已移除，不画 LSTM/GRU 线
print("ℹ️ 不叠加 LSTM/GRU 曲线（模型已移除）")

ax1.axvline(interp_df.index[-1], color='gray', linestyle=':', linewidth=1.0,
            label='History/Forecast split')
ax1.set_title('Forecast of Cumulative Usage & Estimated Cost')
ax1.set_ylabel('Cumulative Usage')
ax1.legend(loc='upper left')
format_time_axis(ax1, "day")

plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "usage_forecast.svg"), format="svg")
plt.close()

# ----------------------------- 7️⃣ 其他图表 -----------------------------
print("📊 正在生成其他图表...")

# ① 累计原始 vs 插值
fig, ax = plt.subplots(figsize=(8, 4))
ax.scatter(df['ts'], df['cum'], color='tab:orange', label='Original readings', s=20)
ax.plot(interp_df.index, interp_df['cumulative_total'], '-', color='tab:orange', label='Interpolated')
ax.set_title('Cumulative Value: Original vs Interpolated')
ax.legend()
format_time_axis(ax)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "usage_cumulative.svg"), format="svg")
plt.close()

# ② Hourly usage + temperature
fig, ax1 = plt.subplots(figsize=(8, 4))

# ✅ 限制显示最近30天
last_date = hourly.index.max()
start_date = last_date - timedelta(days=30)
hourly_30d = hourly[hourly.index >= start_date]

ax1.bar(hourly_30d.index, hourly_30d['hourly_usage'], width=0.03, color='tab:orange', label='Hourly Usage')
ax1.set_ylabel('Units / Hour', color='tab:orange')
ax1.set_title('Hourly Usage & Temperature (Last 30 Days)')

# 读取并绘制室外温度（蓝线）
if not weather_df.empty:
    ax2 = ax1.twinx()
    wsub = weather_df.loc[hourly_30d.index.min(): hourly_30d.index.max()]
    ax2.plot(wsub.index, wsub['temperature'], color='tab:blue', alpha=0.6, label='Outdoor Temp')

    # ✅ 新增：绘制室内温度（绿色插值曲线）
    temp_inside_path = os.path.join(DATA_DIR, "temperature_inside.csv")
    if os.path.exists(temp_inside_path):
        try:
            # 说明：第二列是温度
            tin = pd.read_csv(temp_inside_path, header=None, names=['datetime', 'temp', 'humidity', 'v3'])
            tin['datetime'] = pd.to_datetime(tin['datetime'], errors='coerce')
            tin = tin.dropna(subset=['datetime'])
            tin = tin.set_index('datetime').sort_index()

            # 使用 PchipInterpolator 插值
            from scipy.interpolate import UnivariateSpline
            xi = tin.index.astype(np.int64) / 1e9
            yi = tin['temp'].astype(float).to_numpy()
            interp_fn = UnivariateSpline(xi, yi, s=len(xi) * 0.3, k=3)
            full_time = pd.date_range(tin.index.min(), tin.index.max(), freq='1min')
            yi_interp = interp_fn(full_time.astype(np.int64) / 1e9)

            # ✅ 仅显示30天内的室内温度
            mask = (full_time >= start_date) & (full_time <= last_date)
            ax2.plot(full_time[mask], yi_interp[mask], color='green', linewidth=1.5, alpha=0.8, label='Indoor Temp')
            print(f"✅ 已叠加室内温度曲线 ({mask.sum()} 点, 数据范围 {tin.index.min()} → {tin.index.max()})")
        except Exception as e:
            print(f"⚠️ 室内温度曲线加载失败: {e}")

    ax2.legend(loc='upper right', fontsize=8)

format_time_axis(ax1)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "usage_hourly.svg"), format="svg")
plt.close()

# ③ Daily usage + temperature
fig, ax1 = plt.subplots(figsize=(8, 4))

# ✅ 限制显示最近30天
last_date_daily = daily.index.max()
start_date_daily = last_date_daily - timedelta(days=30)
daily_30d = daily[daily.index >= start_date_daily]

ax1.plot(daily_30d.index, daily_30d['daily_usage'], 'o-', color='tab:orange', label='Daily Usage')
ax1.set_ylabel('Units / Day', color='tab:orange')
ax1.set_title('Daily Usage & Temperature (Last 30 Days)')

if not weather_df.empty and not weather_daily.empty:
    ax2 = ax1.twinx()

    # 提取每日温度（限制30天）
    temp_daily = weather_df['temperature'].resample('D').mean()
    temp_daily_30d = temp_daily[temp_daily.index >= start_date_daily]
    temp_min_30d = weather_daily['tmin'][weather_daily.index >= start_date_daily]
    temp_max_30d = weather_daily['tmax'][weather_daily.index >= start_date_daily]

    # 绘制平均气温（蓝实线）
    ax2.plot(temp_daily_30d.index, temp_daily_30d, color='tab:blue', alpha=0.8, label='Avg Temp (°C)')
    # 绘制最高/最低气温虚线
    ax2.plot(temp_max_30d.index, temp_max_30d, '--', color='tab:blue', alpha=0.5, linewidth=1.0, label='Max Temp')
    ax2.plot(temp_min_30d.index, temp_min_30d, '--', color='tab:blue', alpha=0.5, linewidth=1.0, label='Min Temp')
    # 填充最高/最低之间的区域
    ax2.fill_between(temp_max_30d.index, temp_min_30d, temp_max_30d, color='tab:blue', alpha=0.15, label='Temp Range')

    # 右侧图例
    ax2.legend(loc='upper right', fontsize=8)

format_time_axis(ax1, "day")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "usage_daily.svg"), format="svg")
plt.close()

# ④ 平均小时模式
hourly['hour'] = hourly.index.hour
hourly_pattern = hourly.groupby('hour')['hourly_usage'].mean()
fig, ax = plt.subplots(figsize=(8, 4))
ax.plot(hourly_pattern.index, hourly_pattern.values, 'o-', color='tab:orange')
ax.set_title('Average Hourly Pattern')
ax.set_xlabel('Hour of Day')
ax.set_ylabel('Average Usage (Units/Day)')
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "usage_pattern.svg"), format="svg")
plt.close()

print("✅ 全部数据、预测、CSV 与图表已生成。")

# ⑤ 极坐标24小时模式图
print("📍 正在生成极坐标图...")
hourly_pattern = hourly.groupby(hourly.index.hour)['hourly_usage'].mean()

# ✅ 动态调整坐标范围：以数据范围为中心，上下各扩展20%
r_min = hourly_pattern.min()
r_max = hourly_pattern.max()
r_range = r_max - r_min
r_padding = r_range * 0.2 if r_range > 0 else 0.5
r_limit_min = r_min - r_padding
r_limit_max = r_max + r_padding

fig = plt.figure(figsize=(8, 8))
ax = fig.add_subplot(111, projection='polar')

# 转换为弧度（0点在顶部，顺时针）
theta = np.linspace(0, 2 * np.pi, 24, endpoint=False)
r = hourly_pattern.values * 24  # ✅ 转换为日用量

# 闭合曲线（连接23点和0点）
theta = np.append(theta, theta[0])
r = np.append(r, r[0])

ax.plot(theta, r, 'o-', color='tab:orange', linewidth=2, markersize=6)
ax.fill(theta, r, alpha=0.25, color='tab:orange')

# ✅ 设置径向范围：从下限到上限
ax.set_ylim(r_limit_min * 24, r_limit_max * 24)

# 设置刻度标签
ax.set_theta_zero_location('N')  # 0点在顶部
ax.set_theta_direction(-1)  # 顺时针
ax.set_xticks(np.linspace(0, 2 * np.pi, 24, endpoint=False))
ax.set_xticklabels([f'{h:02d}:00' for h in range(24)], fontsize=9)
ax.set_title('24-Hour Usage Pattern (Polar)', pad=20, fontsize=12, fontweight='bold')
ax.set_ylabel('Average Usage (Units/Day)', labelpad=30)

plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "usage_pattern_polar.svg"), format="svg")
plt.close()
print("✅ 已生成 usage_pattern_polar.svg")

# ⑥ 热力图：日期 x 小时
print("🔥 正在生成热力图...")
hourly_pivot = hourly.copy()
hourly_pivot['date'] = hourly_pivot.index.date
hourly_pivot['hour'] = hourly_pivot.index.hour

# 透视表：行=日期，列=小时
heatmap_data = hourly_pivot.pivot_table(
    values='hourly_usage',
    index='date',
    columns='hour',
    aggfunc='mean'
)

# ✅ 根据日期数量动态调整图表高度
num_days = len(heatmap_data)
height = max(8, num_days * 0.3)  # 每天 0.3 英寸，最小 8 英寸

fig, ax = plt.subplots(figsize=(12, height))
im = ax.imshow(heatmap_data.values, cmap='YlOrRd', aspect='auto', interpolation='nearest')

# 设置坐标轴
ax.set_xticks(range(24))
ax.set_xticklabels([f'{h:02d}' for h in range(24)])

# ✅ 优化 y 轴标签显示
if num_days <= 30:
    ax.set_yticks(range(len(heatmap_data)))
    ax.set_yticklabels([str(d) for d in heatmap_data.index], fontsize=7)
else:
    # 超过 30 天时，每隔几天显示一个标签
    step = max(1, num_days // 15)  # 最多显示 15 个标签
    ax.set_yticks(range(0, len(heatmap_data), step))
    ax.set_yticklabels([str(heatmap_data.index[i]) for i in range(0, len(heatmap_data), step)], fontsize=7)

ax.set_xlabel('Hour of Day')
ax.set_ylabel('Date')
ax.set_title('Hourly Usage Heatmap')

# 颜色条
cbar = plt.colorbar(im, ax=ax)
cbar.set_label('Usage (Units/Hour)', rotation=270, labelpad=20)

plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "usage_heatmap.svg"), format="svg")
plt.close()
print("✅ 已生成 usage_heatmap.svg")

print("✅ 全部图表已生成完毕！")
