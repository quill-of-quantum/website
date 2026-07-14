import numpy as np
import pandas as pd
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import SimpleRNN, Dense
from tensorflow.keras.optimizers import Adam


# -------------------------------
# 构造训练序列
# -------------------------------
def build_sequences(df, n_steps=4):
    """
    df 必须包含列：tavg, tmin, tmax, usage
    """
    X, y = [], []
    for i in range(len(df) - n_steps):
        X.append(df.iloc[i:i+n_steps][["tavg", "tmin", "tmax", "usage"]].values)
        y.append(df["usage"].iloc[i+n_steps])
    return np.array(X), np.array(y)


# -------------------------------
# 训练 RNN（极简版）
# -------------------------------
def train_rnn(df_train, n_steps=4, epochs=200):
    X, y = build_sequences(df_train, n_steps)

    model = Sequential()
    model.add(SimpleRNN(32, activation="tanh", return_sequences=False,
                        input_shape=(n_steps, 4)))
    model.add(Dense(16, activation="relu"))
    model.add(Dense(1))

    model.compile(loss="mse", optimizer=Adam(0.01))

    model.fit(X, y, epochs=epochs, batch_size=8, verbose=0)

    return model


# -------------------------------
# 滚动预测
# -------------------------------
def rolling_predict(model, df_hist, df_future_temp, end_date, n_steps=4):
    """
    df_hist：历史含 usage 的
    df_future_temp：未来只有 tavg/tmin/tmax 的
    end_date：预测到哪天
    """
    df_pred = df_hist.copy()

    cur_date = df_hist.index.max() + pd.Timedelta(days=1)

    while cur_date <= end_date:
        # 如果未来温度没有这一天 → 用前一天温度填补（保证不中断）
        if cur_date not in df_future_temp.index:
            tavg = df_future_temp["tavg"].iloc[-1]
            tmin = df_future_temp["tmin"].iloc[-1]
            tmax = df_future_temp["tmax"].iloc[-1]
        else:
            tavg = df_future_temp.loc[cur_date, "tavg"]
            tmin = df_future_temp.loc[cur_date, "tmin"]
            tmax = df_future_temp.loc[cur_date, "tmax"]

        last_n = df_pred.iloc[-n_steps:][["tavg", "tmin", "tmax", "usage"]].values
        X = last_n.reshape(1, n_steps, 4)

        pred_usage = float(model.predict(X, verbose=0)[0])

        df_pred.loc[cur_date, "tavg"] = tavg
        df_pred.loc[cur_date, "tmin"] = tmin
        df_pred.loc[cur_date, "tmax"] = tmax
        df_pred.loc[cur_date, "usage"] = pred_usage

        cur_date += pd.Timedelta(days=1)

    return df_pred["usage"].loc[df_hist.index.max()+pd.Timedelta(days=1):end_date]