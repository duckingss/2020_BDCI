# -*- coding: utf-8 -*-
"""baseline.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/github/siliconx/serverless/blob/main/baseline.ipynb
"""

import os
import numpy as np
import pandas as pd
from tqdm import tqdm
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import KFold
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis as LDA
import lightgbm as lgb

# 常量定义
NFOLDS = 5  # 交叉验证的折数
SEQ_LEN = 5  # 序列长度
WINDOW_SIZE = 2 * SEQ_LEN  # 窗口长度
MODEL_N = 10  # 10个模型分别预测 CPU_USAGE_6...LAUNCHING_JOB_NUMS_10

__author__ = 'siliconx'
__version__ = '1.0.0'

pd.options.display.max_columns = None  # 展示所有列

# 初始数据
RAW_TRAIN = '../data/train/train.csv'
RAW_TEST = '../data/evaluate/evaluation_public.csv'
SAMPLE_SUBMIT = '../data/submit_example.csv'

"""# 1. 加载数据"""

# 加载原始数据
train_df = pd.read_csv(RAW_TRAIN)
test_df = pd.read_csv(RAW_TEST)
sample_df = pd.read_csv(SAMPLE_SUBMIT)

train_df = train_df.sort_values(by=['QUEUE_ID', 'DOTTING_TIME']).reset_index(drop=True)
test_df = test_df.sort_values(by=['ID', 'DOTTING_TIME']).reset_index(drop=True)

# display(train_df, test_df)

"""# 2. 预处理"""


def myLDA(train, test):
    numpad = ['QUEUE_ID', 'CPU_USAGE', 'LJOB', 'RJOB', 'SJOB',
              'CJOB', 'FJOB', 'DOTTING_TIME','STATUS','DISK_USAGE']
    ntrain, ntest = train[numpad], test[numpad]
    data = ntrain.append(ntest)
    x, y = data.values, data['QUEUE_ID']
    lda = LDA(n_components=5)
    lda.fit(x, y)
    add_train, add_test = lda.transform(ntrain.values), lda.transform(ntest.values)
    for i in range(1, 6):
        train[f'x_{i}'] = add_train[:, i - 1]*50
        test[f'x_{i}'] = add_test[:, i - 1]*50


def digitalization(fields):
    """将非数值型域转换为数值型."""
    # 组合训练集和测试集，只用来构建编码器，不用来训练模型
    df = pd.concat([train_df[fields], test_df[fields]], ignore_index=True)

    for f in fields:
        # 构建编码器
        le = LabelEncoder()
        le.fit(df[f])

        # 设置新值
        train_df[f] = le.transform(train_df[f])
        test_df[f] = le.transform(test_df[f])
        print('%s:' % f, le.classes_)


def pre_processing():
    """预处理."""
    print('Preprocessing...')

    # 缺失值填充
    # 经检验，为NaN的都是vm（通过QUEUE_ID查找）
    train_df['RESOURCE_TYPE'].fillna('vm', inplace=True)

    # 观察数据，填充0比较合理（NaN集中在数据前面，可能是由服务器尚未开始运行导致的）
    train_df['DISK_USAGE'].fillna(0, inplace=True)

    # 需要转换的列
    fields = ['STATUS', 'QUEUE_TYPE', 'PLATFORM', 'RESOURCE_TYPE']

    # 数值化
    digitalization(fields)

    # 重命名，原来的名字太长了
    for df in [train_df, test_df]:
        df.rename(columns={
            'LAUNCHING_JOB_NUMS': 'LJOB',
            'RUNNING_JOB_NUMS': 'RJOB',
            'SUCCEED_JOB_NUMS': 'SJOB',
            'CANCELLED_JOB_NUMS': 'CJOB',
            'FAILED_JOB_NUMS': 'FJOB'
        }, inplace=True)


pre_processing()
myLDA(train_df, test_df)
"""# 3. 特征工程

### 3.1 时间特征

-- 把DOTTINGTIME转换为一天之内的时间
"""

for df in [train_df, test_df]:
    t = pd.to_datetime(df['DOTTING_TIME'], unit='ms')

    # 转成小时
    df['DOTTING_TIME'] = t.dt.hour + t.dt.minute / 60

"""### 3.2 行统计特征"""

used_features = ['CPU_USAGE', 'MEM_USAGE', 'DISK_USAGE', 'LJOB', 'RJOB']

# 分组，只用训练集数据做统计
group_data = train_df.groupby(by=['QUEUE_ID'])[used_features]

# 聚合函数
methods = {
    'AVG': 'mean',
    'MEDIAN': 'median',
    'MIN': 'min',
    'MAX': 'max',
    'STD': 'std',
}

for m in methods:
    agg_data = group_data.agg(methods[m])
    agg_data.fillna(method='ffill', inplace=True)
    agg_data.fillna(0, inplace=True)
    agg_data = agg_data.rename(lambda x: 'QUEUE_%s_%s' % (x, m), axis=1)
    agg_data = agg_data.reset_index()

    for df in [train_df, test_df]:
        merged_data = df[['QUEUE_ID']].merge(agg_data, how='left', on=['QUEUE_ID'])
        merged_data.drop(columns=['QUEUE_ID'], inplace=True)

        # 插入新的列
        for c in merged_data.columns:
            df[c] = 0

        # 赋值
        df.loc[:, list(merged_data.columns)] = merged_data.values

"""###    3.3 滑动窗口构造数据集"""

# 需要滑动的数值特征
num_features = ['CPU_USAGE', 'MEM_USAGE', 'DISK_USAGE',
                'LJOB', 'RJOB', 'SJOB', 'CJOB', 'FJOB']

# 需要预测的值
y_features = ['CPU_USAGE', 'LJOB']

# 生成测试集时间窗数据
for i in range(SEQ_LEN):
    for sf in num_features:
        new_f = '%s_%d' % (sf, i + 1)
        test_df[new_f] = test_df[sf].shift(-i)

# 删除原来的列
test_df.drop(columns=num_features, inplace=True)

# 只取每个ID的第一条数据
test_df = test_df.groupby(by='ID', as_index=False).first()

# 生成训练集时间窗数据
temp = pd.DataFrame()
qids = sorted(train_df['QUEUE_ID'].unique())

for qid in tqdm(qids):  # 按QUEUE_ID进行处理
    queue = train_df[train_df['QUEUE_ID'] == qid].copy(deep=True)

    # 生成时间窗数据
    for i in range(SEQ_LEN):
        for sf in num_features:
            new_f = '%s_%d' % (sf, i + 1)
            queue[new_f] = queue[sf].shift(-i)

    # 处理需要预测的值
    for i in range(SEQ_LEN):
        for y in y_features:
            new_y = '%s_%d' % (y, i + SEQ_LEN + 1)
            queue[new_y] = queue[y].shift(-i - SEQ_LEN)

    # 删除原来的列
    queue.drop(columns=num_features, inplace=True)

    # 对于每个QUEUE_ID，丢弃最后10条有NAN值的数据
    queue = queue.head(queue.shape[0] - WINDOW_SIZE)
    temp = temp.append(queue)

# 重设索引
train_df = temp.reset_index(drop=True)

"""### 3.4 列统计特征"""

cpu_usages = []
mem_usages = []
disk_usages = []
ljobs = []
rjobs = []

for i in range(SEQ_LEN):
    postfix = '_%d' % (i + 1)
    cpu_usages.append('CPU_USAGE' + postfix)
    mem_usages.append('MEM_USAGE' + postfix)
    disk_usages.append('DISK_USAGE' + postfix)
    ljobs.append('LJOB' + postfix)
    rjobs.append('RJOB' + postfix)

for df in [train_df, test_df]:
    # zheng.heng baseline给的特征
    df['USED_CPU'] = df['CU'] * df['CPU_USAGE_5'] / 100
    df['USED_MEM'] = 4 * df['CU'] * df['MEM_USAGE_5'] / 100
    df['TO_RUN_JOBS'] = df['LJOB_5'] - df['RJOB_5']
    df.loc[df['TO_RUN_JOBS'] < 0, 'TO_RUN_JOBS'] = 0

    # zheng.heng baseline中的新的列特征
    pairs = [
        ('CPU', 'CPU_USAGE', cpu_usages),
        ('MEM', 'MEM_USAGE', mem_usages),
        ('DISK', 'DISK_USAGE', disk_usages),
        ('LJOB', 'LJOB', ljobs),
        ('RJOB', 'RJOB', rjobs),
    ]

    for short_name, f, usages in pairs:
        df[short_name + '_AVG'] = df[usages].mean(axis=1)
        df[short_name + '_STD'] = df[usages].std(axis=1)
        df[short_name + '_DIFF'] = df['%s_5' % f] - df['%s_1' % f]

"""### 3.5 特征过滤"""

# 去掉无用列
useless = [
    'QUEUE_ID', 'PLATFORM', 'RESOURCE_TYPE', 'STATUS',
]

train_df.drop(columns=useless, inplace=True)
test_df.drop(columns=['ID'] + useless, inplace=True)

# display(train_df, test_df)

"""# 4. 训练&预测"""

Y_features = [
    'CPU_USAGE_6', 'LJOB_6',
    'CPU_USAGE_7', 'LJOB_7',
    'CPU_USAGE_8', 'LJOB_8',
    'CPU_USAGE_9', 'LJOB_9',
    'CPU_USAGE_10', 'LJOB_10'
]

Y_train = train_df[Y_features]
train_df.drop(columns=Y_features, inplace=True)

lgb_param = {
    'num_leaves': 41,
    'max_depth': 5,
    'learning_rate': 0.08,
    'n_estimators': 150,
    'subsample': 0.9,
    'feature_fraction': 0.8,
    'reg_alpha': 0.6,
    'reg_lambda': 1.2,
    'seed': 2020
}

# 总迭代次数
N = MODEL_N * NFOLDS

# 进度条
pbar = tqdm(total=N, position=0, leave=True)

# 交叉验证
kfold = KFold(n_splits=NFOLDS, shuffle=True, random_state=2020)
kf = kfold.split(train_df)

# out-of-folds
oof = np.zeros((train_df.shape[0], MODEL_N))

for train_idx, validate_idx in kf:
    # 切割训练集&验证集
    X_train, y_train = train_df.iloc[train_idx, :], Y_train.iloc[train_idx, :]
    X_valid, y_valid = train_df.iloc[validate_idx, :], Y_train.iloc[validate_idx]

    for i in range(MODEL_N):
        y = y_train.iloc[:, i]

        reg = lgb.LGBMRegressor(n_jobs=-1, **lgb_param)
        bst = reg.fit(X_train, y)

        # 验证集
        valid_pred = bst.predict(X_valid)
        valid_pred[valid_pred < 0] = 0
        valid_pred[valid_pred > 100] = 100
        valid_pred = valid_pred.astype(np.int)
        oof[validate_idx, i] = valid_pred

        # 测试集
        test_pred = bst.predict(test_df)
        test_pred[test_pred < 0] = 0
        test_pred[test_pred > 100] = 100
        sample_df.iloc[:, i + 1] += test_pred / NFOLDS

        # 更新进度条
        pbar.update(1)

# 关闭进度条
pbar.close()

# 转为整型
sample_df = sample_df.astype(np.int)


def evaluate(Y_true, Y_preds):
    """赛题给的评估函数."""
    # shape: (n, 10)
    if not isinstance(Y_true, np.ndarray):
        Y_true = Y_true.to_numpy()

    if not isinstance(Y_preds, np.ndarray):
        Y_preds = Y_preds.to_numpy()

    dist = 0  # DIST_k
    for i in range(MODEL_N // 2):
        cpu_true, job_true = Y_true[:, i * 2], Y_true[:, i * 2 + 1]  # shape: (n,)
        cpu_preds, job_preds = Y_preds[:, i * 2], Y_preds[:, i * 2 + 1]  # shape: (n,)
        max_job = np.max((job_true, job_preds), axis=0)

        # 防止分母为0（当分母为0是，分子也为0，所以可以把分母0设为1）
        max_job[max_job == 0] = 1.0
        dist += 0.9 * np.abs((cpu_preds - cpu_true) / 100) + 0.1 * np.abs((job_true - job_true) / max_job)

    score = 1 - dist.mean()
    return score


# 计算验证集分数
oof_score = evaluate(Y_train, oof)
print('oof score = %.6f' % oof_score)  # 0.909830


sample_df.to_csv('baseline_lda.csv', index=False)
